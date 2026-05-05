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

    df['days_in_month'] = df.index.days_in_month

    df['daily_kwh'] = monthly_bill_kwh / df['days_in_month'] 

    conditions = [
        (df.index.hour >= 0) & (df.index.hour < 6),   # Night
        (df.index.hour >= 6) & (df.index.hour < 9),   # Morning
        (df.index.hour >= 9) & (df.index.hour < 17),  # Day
        (df.index.hour >= 17) & (df.index.hour < 22), # Evening Peak
        (df.index.hour >= 22)                     # Late Night
    ]

    hourly_percentage = [0.025, 0.066, 0.025, 0.070, 0.050] #rough estimate change afterwards

    df['consumption_kwh'] = np.select(conditions,hourly_percentage) * df['daily_kwh']
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
    battery_col_idx = df.columns.get_loc('battery_level')

    solar_col_idx = df.columns.get_loc('energy_kwh')
    cons_col_idx = df.columns.get_loc('consumption_kwh')

    for i in range(len(df)):
        solar = df.iat[i,solar_col_idx]
        cons = df.iat[i,cons_col_idx]

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

        df.iat[i, battery_col_idx] = current_battery

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
        print("\n[3] Generating Monthly Financial Summary...")
        monthly_data = df.resample('M').sum() # Sum up all the hours for each month

        # Create a brand new DataFrame specifically for our final CSV report
        summary_df = pd.DataFrame()
        
        # Pull the summed data into our clean report
        summary_df['Total_Consumption_kWh'] = monthly_data['consumption_kwh']
        summary_df['Solar_Generation_kWh'] = monthly_data['energy_kwh']
        summary_df['Grid_Import_kWh'] = monthly_data['from_ceb_import']
        summary_df['Grid_Export_kWh'] = monthly_data['to_ceb_export']
        summary_df['Wasted_Energy_kWh'] = monthly_data['wasted_energy']

        # --- 5. CALCULATE FINANCIALS FOR EVERY SINGLE MONTH ---
        # We use .apply() to run your JSON tariff function on every row individually!
        
        # 1. What would the bill be WITHOUT solar?
        summary_df['Old_Bill_Rs'] = summary_df['Total_Consumption_kWh'].apply(
            lambda units: calculate_no_solar_bill(units, my_tariff)
        )
        
        # 2. What is the new bill WITH solar? (Only paying for what we imported)
        summary_df['New_Bill_Rs'] = summary_df['Grid_Import_kWh'].apply(
            lambda units: calculate_no_solar_bill(units, my_tariff)
        )
        
        # 3. Revenue from selling to CEB (Rs 3.00 per kWh)
        summary_df['Export_Revenue_Rs'] = summary_df['Grid_Export_kWh'] * 3.00
        
        # 4. Final out-of-pocket cost and Savings
        summary_df['Final_Net_Bill_Rs'] = summary_df['New_Bill_Rs'] - summary_df['Export_Revenue_Rs']
        summary_df['Total_Savings_Rs'] = summary_df['Old_Bill_Rs'] - summary_df['Final_Net_Bill_Rs']

        # --- 6. PRINT A QUICK SUMMARY & SAVE CSVs ---
        
        # Print total project savings to the console
        total_cash_saved = summary_df['Total_Savings_Rs'].sum()
        total_months = len(summary_df)
        print("\n" + "="*50)
        print("  ☀️ MAURITIUS CEB SOLAR BREAK-EVEN REPORT ☀️  ")
        print("="*50)
        print(f"Total Months Simulated : {total_months} months")
        print(f"Total Cash Saved       : Rs {total_cash_saved:,.2f}")
        print(f"Average Monthly Saving : Rs {(total_cash_saved/total_months):,.2f}")
        print("="*50)

        # SAVE FILE 1: The Hourly Data (for debugging/physics analysis)
        hourly_path = Path.home() / "Downloads" / "Solar_Hourly_Raw.csv"
        df.to_csv(hourly_path)
        
        # SAVE FILE 2: The Beautiful Monthly Financial Report (for investors/users)
        monthly_path = Path.home() / "Downloads" / "Solar_Monthly_Financials.csv"
        summary_df.to_csv(monthly_path)
        
        print(f"\nFiles successfully saved!")
        print(f"1. Hourly Physics Data: {hourly_path}")
        print(f"2. Monthly Financials : {monthly_path}")


    else:
        print("Could not retrieve data.")


