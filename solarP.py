import requests
from pathlib import Path

base_url = "https://power.larc.nasa.gov/api/temporal/daily/point"
# base_url2= "/api/temporal/daily/point?parameters=T2M&community=SB&longitude=0&latitude=0&start=20170101&end=20170201&format=JSON"
LATITUDE = "-20.16"  #-90 to 90
LONGITUDE = "57.55"
START_DATE = "20200101" #YYYYMMDD
END_DATE = "20260101"


def fetch_nasa_data(lat,lon,start,end):
    params = {
    "parameters": "ALLSKY_SFC_SW_DWN,T2M",
    "community":"RE",
    "longitude": lon,
    "latitude": lat,
    "start":start,
    "end":end,
    "format":"CSV"
    }

    try:
        response = requests.get(base_url,params=params,timeout=10)
        response.raise_for_status()

        return response.text
    
    except requests.exceptions.RequestException as e:
        print(f"Oops! Something went wrong: {e}")
        return None
    


if __name__ == "__main__":
    data = fetch_nasa_data(LATITUDE,LONGITUDE,START_DATE,END_DATE)

    if data:
        print("Data retrieved successfully!")

        download_Path = Path.home() / "Downloads" / "nasa_data.csv"

        with open(download_Path,"w") as file:
            file.write(data)
        
        print(f"File saved as {download_Path}")



