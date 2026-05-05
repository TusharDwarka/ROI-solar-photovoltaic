import pvlib
import pandas as pd
from pathlib import Path
import numpy as np
import calendar
from datetime import date
import json
import os

current_dir = Path(__file__).parent
tariff_file = current_dir / "cebTariff" / "tariffs.json"

with open(tariff_file,'r') as f:
    normal_tariff = json.load(f)


LATITUDE = -20.16  #-90 to 90
LONGITUDE = 57.55
START_DATE = 2020 #YYYYMMDD
END_DATE = 2023

def calculate_no_solar_bill(units,tariff_code):
    tariff_data = normal_tariff['tariffs'][tariff_code]

    if tariff_data['blocks'] == 'standard_blocks':
        blocks = normal_tariff['rates']['standard_blocks']
    else:
        blocks=normal_tariff['rates']['social_starter_blocks_110A']
    
    energy_cost = 0
    remaining_units = units

    for limit,rate in blocks:
        if remaining_units > limit:
            energy_cost += limit * rate
            remaining_units -= limit
        else:
            energy_cost += remaining_units * rate
            remaining_units = 0
            break
    
    bill_after_min = max(energy_cost, tariff_data['min_charge'])

    total_bill = bill_after_min + tariff_data['mbc_fee'] + normal_tariff['meter_rental']

    return total_bill

def get_solar_data():

    try:
        data,metadata = pvlib.iotools.get_pvgis_hourly(
            latitude = LATITUDE,
            longitude= LONGITUDE,
            start= START_DATE,
            end=END_DATE,
            raddatabase='PVGIS-SARAH3',
            components=True,
            map_variables=True 
        )
        return data
    
    except Exception as e:
        print(f"Failed to fetch data: {e}")
        return None

def analyse_data(df):
    df["poa_global"] = df['poa_direct'] + df['poa_sky_diffuse'] + df['poa_ground_diffuse']

    avg_global = df["poa_global"].mean()
    avg_temp = df['temp_air'].mean()

    print("\n--- Analysis Results ---")
    print(f"Average Global Irradiance: {avg_global:.2f} W/m²")
    print(f"Average Temperature:       {avg_temp:.2f} °C")

    # convert into kWh
    panel_area = 20 
    efficiency = 0.20 
    pr = 0.85    #performance ratio /Heatloss etc

    df['energy_kwh'] = (df['poa_global'] * panel_area * efficiency * pr) / 1000

    monthly_energy = df['energy_kwh'].resample('M').sum()  #reassemble by month cause its in hour

    #print("\n--- Monthly Solar Generation (kWh) ---")
    #print(monthly_energy)
    
    return df


def hourly_calculation(df, monthly_bill_kwh, inverter_kw , battery_max_kwh):

    daily_kwh = monthly_bill_kwh/30   # change for better calculation according to year and month

    conditions = [
        (df.index.hour >= 0) & (df.index.hour < 6),   # Night
        (df.index.hour >= 6) & (df.index.hour < 9),   # Morning
        (df.index.hour >= 9) & (df.index.hour < 17),  # Day
        (df.index.hour >= 17) & (df.index.hour < 22), # Evening Peak
        (df.index.hour >= 22)                     # Late Night
    ]

    hourly_percentage = [0.025, 0.066, 0.025, 0.070, 0.050] #rough estimate change afterwards

    df['consumption_kwh'] = np.select(conditions,hourly_percentage) * daily_kwh
    df['battery_level'] = 0.0
    df['to_ceb_export'] = 0.0
    df['from_ceb_import'] = 0.0
    df['wasted_energy'] = 0.0

    current_battery = 0.0 # Battery starts empty
    max_export = inverter_kw * 0.5 # CEB rule: 50% limit

    #for loop for column index
    export_col_idx = df.columns.get_loc('to_ceb_export')
    wasted_col_idx = df.columns.get_loc('wasted_energy')
    import_col_idx = df.columns.get_loc('from_ceb_import')

    for i in range(len(df)):
        solar = df['energy_kwh'].iloc[i]
        cons = df['consumption_kwh'].iloc[i]

        net = solar - cons

        if net > 0:  # meaning producing extra kwh
            space_left = battery_max_kwh - current_battery
            to_battery = min(net,space_left)
            current_battery +=to_battery
            
            #export to ceb
            leftover_after_battery = net - to_battery
            to_ceb = min(leftover_after_battery, max_export)

            #wasted kwh
            wasted = leftover_after_battery - to_ceb

            df.iat[i,export_col_idx] = to_ceb
            df.iat[i, wasted_col_idx] = wasted

        else:
            needed = abs(net)
            from_battery = min(needed, current_battery)
            current_battery -=from_battery

            from_ceb = needed - from_battery
            df.iat[i,import_col_idx] = from_ceb

        df.iat[i, df.columns.get_loc('battery_level')] = current_battery

    return df


if __name__ == "__main__" :

    units_used = 339           # User's average monthly CEB bill (kWh)
    my_tariff = "120"          # User's CEB tariff code
    system_kwp = 5.0           # Size of the solar panels (kW)
    inverter_kw = 5.0          # Size of the inverter (kW)
    battery_kwh = 5.0          # Size of the battery (kWh)

    print("\n[1] Fetching PVGIS Solar Data for Mauritius...")
    df = get_solar_data()

    if df is not None:
        print("Data retrieved successfully!")
        df.index = df.index + pd.Timedelta(hours=4)  #fix timezone mauritius
        # print(df.head())

        #monthly
        df = analyse_data(df)

        print("\n[2] Simulating Battery and CEB Grid Rules...")
        df = hourly_calculation(
            df, 
            monthly_bill_kwh=units_used, 
            inverter_kw=inverter_kw, 
            battery_max_kwh=battery_kwh
        )

        #group by month
        monthly_data = df.resample('M').sum()
        avg_solar_gen = monthly_data['energy_kwh'].mean()
        avg_import_ceb = monthly_data['from_ceb_import'].mean()
        avg_export_ceb = monthly_data['to_ceb_export'].mean()
        avg_wasted = monthly_data['wasted_energy'].mean()

        print("\n[3] Calculating Financials...")

        old_bill_rs = calculate_no_solar_bill(units_used, my_tariff)
        new_bill_rs = calculate_no_solar_bill(avg_import_ceb, my_tariff)

        export_revenue = avg_export_ceb * 3.00

        final_net_bill = new_bill_rs - export_revenue
        

        print("\n" + "="*45)
        print("  ☀️ MAURITIUS CEB SOLAR BREAK-EVEN REPORT ☀️  ")
        print("="*45)
        print(f"System Size: {system_kwp} kWp Panels | {battery_kwh} kWh Battery")
        print("-" * 45)
        print("ENERGY METRICS (Average Monthly):")
        print(f"  Old Household Consumption : {units_used:.2f} kWh")
        print(f"  Solar Energy Generated    : {avg_solar_gen:.2f} kWh")
        print(f"  New CEB Grid Import       : {avg_import_ceb:.2f} kWh (Bought from CEB)")
        print(f"  New CEB Grid Export       : {avg_export_ceb:.2f} kWh (Sold to CEB)")
        print(f"  Wasted/Clipped Energy     : {avg_wasted:.2f} kWh (Inverter limit hit)")
        print("-" * 45)
        print("FINANCIAL METRICS (Average Monthly):")
        print(f"  Old CEB Bill              : Rs {old_bill_rs:.2f}")
        print(f"  New CEB Bill (Import)     : Rs {new_bill_rs:.2f}")
        print(f"  Revenue from Export       : Rs {export_revenue:.2f} (Rs 3.00/kWh rate)")
        print(f"  FINAL NET CEB BILL        : Rs {final_net_bill:.2f}")
        print("-" * 45)
        print(f"💰 TOTAL MONTHLY SAVINGS   : Rs {(old_bill_rs - final_net_bill):.2f} 💰")
        print("="*45)

        

        download_path = Path.home() / "Downloads" / "Solar.csv"

        df.to_csv(download_path)
        print(f"File saved as {download_path}")


    else:
        print("Could not retrieve data.")


