import pvlib
import pandas as pd
from pathlib import Path
import numpy as np
import calendar
from datetime import date

LATITUDE = -20.16  #-90 to 90
LONGITUDE = 57.55
START_DATE = 2020 #YYYYMMDD
END_DATE = 2023

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

    print("\n--- Monthly Solar Generation (kWh) ---")
    print(monthly_energy)
    
    return monthly_energy


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
        cons = df['consumption'].iloc[i]

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












    


if __name__ == "__main__" :
    df = get_solar_data()

    if df is not None:
        print("Data retrieved successfully!")
        print(df.head())
        monthly_solar = analyse_data(df)

        download_path = Path.home() / "Downloads" / "Solar.csv"

        df.to_csv(download_path)
        print(f"File saved as {download_path}")


    else:
        print("Could not retrieve data.")


