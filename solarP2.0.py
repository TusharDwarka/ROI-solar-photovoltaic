import pvlib
import pandas as pd
from pathlib import Path

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


if __name__ == "__main__" :
    df = get_solar_data()

    if df is not None:
        print("Data retrieved successfully!")
        print(df.head())
        analyse_data(df)

        download_path = Path.home() / "Downloads" / "Solar.csv"

        df.to_csv(download_path)
        print(f"File saved as {download_path}")


    else:
        print("Could not retrieve data.")


