import pvlib
import pandas as pd
from pathlib import Path
import numpy as np
import calendar
from datetime import date
import json
import os
import matplotlib.pyplot as plt

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

def analyse_data(df, system_kwp):
    df["poa_global"] = df['poa_direct'] + df['poa_sky_diffuse'] + df['poa_ground_diffuse']

    pr = 0.85    #performance ratio /Heatloss etc

    df['energy_kwh'] = (df['poa_global'] / 1000) * system_kwp * pr
    
    return df

def estimate_units_from_bill(bill_amount, tariff_code):
    """
    Takes a total bill in Rs and reverse-calculates the approximate kWh units used.
    """
    tariff_data = normal_tariff['tariffs'][tariff_code]

    if tariff_data['blocks'] == 'standard_blocks':
        blocks = normal_tariff['rates']['standard_blocks']
    else:
        blocks = normal_tariff['rates']['social_starter_blocks_110A']
    
    # 1. Strip away the fixed fees to find the pure energy cost
    mbc_fee = tariff_data['mbc_fee']
    meter_rental = normal_tariff['meter_rental']
    min_charge = tariff_data['min_charge']
    
    target_energy_cost = bill_amount - mbc_fee - meter_rental
    
    # Handle edge case: if they input a bill that is just the minimum charge
    if target_energy_cost <= min_charge:
        target_energy_cost = min_charge
    
    units_estimated = 0
    remaining_cost = target_energy_cost
    
    # 2. Reverse through the blocks
    for limit, rate in blocks:
        # Calculate the maximum money this block can charge
        max_cost_for_block = limit * rate
        
        # If our remaining money is bigger than this block's max cost, 
        # we used up this entire block of units.
        if remaining_cost > max_cost_for_block:
            remaining_cost -= max_cost_for_block
            units_estimated += limit
        else:
            # If the remaining money is smaller, we only used a fraction of this block.
            # Money / Rate = Units
            units_estimated += (remaining_cost / rate)
            remaining_cost = 0
            break  # We are done!
            
    return units_estimated


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

def calculate_cst(row):
    consumption = row['Total_Consumption_kWh']
    offset = row['Energy_Offset']
    if consumption <= 500:
        return 0.00
    elif consumption <= 1000:
        return offset * 0.82
    else:
        return offset * 1.63


if __name__ == "__main__" :


    monthly_bill_rs = 2300
    #units_used = 339           # User's average monthly CEB bill (kWh)
    my_tariff = "120"          # User's CEB tariff code
    system_kwp = 2.5           # Size of the solar panels (kW)
    inverter_kw = 5.0          # Size of the inverter (kW)
    battery_kwh = 5.0          # Size of the battery (kWh)

    units_used = estimate_units_from_bill(monthly_bill_rs, my_tariff)
    print(units_used)

    print("\n[1] Fetching PVGIS Solar Data for Mauritius...")
    df = get_solar_data()

    if df is not None:
        print("Data retrieved successfully!")
        df.index = df.index + pd.Timedelta(hours=4)  #fix timezone mauritius
        # print(df.head())

        #monthly
        df = analyse_data(df,system_kwp)

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
        
        # 1. What would the bill be WITHOUT solar?
        summary_df['Old_Bill_Rs'] = summary_df['Total_Consumption_kWh'].apply(
            lambda units: calculate_no_solar_bill(units, my_tariff)
        )

        # =========================================================
        # NET-METERING CALCULATION (Tariff 150A_NM)
        # =========================================================
        # We only pay for what the battery/solar couldn't cover (Import)  Else get paid for what we export (Export) ad use Solar for free (Consumption)
        
        # 2. What is the new bill WITH solar? (Only paying for what we imported)
        summary_df['Net_Meter_Bill'] = summary_df['Grid_Import_kWh'].apply(
            lambda units: calculate_no_solar_bill(units, my_tariff)
        )

        summary_df['Energy_Offset'] = summary_df['Total_Consumption_kWh'] - summary_df['Grid_Import_kWh']
        

        summary_df['CST_Tax_Rs'] = summary_df.apply(calculate_cst, axis=1)
        
        # Revenue from excess sold at Rs 3.00
        summary_df['Net_Export_Revenue'] = summary_df['Grid_Export_kWh'] * 3.00
        
        # Final Net Metering Bill
        summary_df['Final_Net_Metering_Bill_Rs'] = summary_df['Net_Meter_Bill'] + summary_df['CST_Tax_Rs'] - summary_df['Net_Export_Revenue']
        summary_df['Net_Metering_Savings'] = summary_df['Old_Bill_Rs'] - summary_df['Final_Net_Metering_Bill_Rs']

        # =========================================================
        # GROSS-METERING CALCULATION (Sell everything at Rs 4.20)
        # =========================================================
        summary_df['Gross_Export_Revenue'] = summary_df['Solar_Generation_kWh'] * 4.20
        summary_df['Final_Gross_Metering_Bill_Rs'] = summary_df['Old_Bill_Rs'] - summary_df['Gross_Export_Revenue']
        summary_df['Gross_Metering_Savings'] = summary_df['Old_Bill_Rs'] - summary_df['Final_Gross_Metering_Bill_Rs']

        #print summary
        
        # print total project
        total_net_savings = summary_df['Net_Metering_Savings'].sum()
        total_gross_savings = summary_df['Gross_Metering_Savings'].sum()
        
        print("\n" + "="*55)
        print(" ☀️ MAURITIUS CEB SOLAR BREAK-EVEN REPORT ☀️ ")
        print("="*55)
        print(f"Total Months Simulated : {len(summary_df)} months")
        print("-" * 55)
        print("💰 OPTION 1: NET-METERING (Use first, sell excess at Rs 3.00)")
        print(f"Total Cash Saved       : Rs {total_net_savings:,.2f}")
        print(f"Average Monthly Saving : Rs {(total_net_savings/len(summary_df)):,.2f}")
        print("-" * 55)
        print("💰 OPTION 2: GROSS-METERING (Sell everything at Rs 4.20)")
        print(f"Total Cash Saved       : Rs {total_gross_savings:,.2f}")
        print(f"Average Monthly Saving : Rs {(total_gross_savings/len(summary_df)):,.2f}")
        print("="*55)

        # SAVE FILE 1: The Hourly Data (for debugging/physics analysis)
        hourly_path = Path.home() / "Downloads" / "Solar_Hourly_Raw.csv"
        df.to_csv(hourly_path)
        
        # SAVE FILE 2: The Beautiful Monthly Financial Report (for investors/users)
        monthly_path = Path.home() / "Downloads" / "Solar_Monthly_Financials.csv"
        summary_df.to_csv(monthly_path)
        
        print(f"\nFiles successfully saved!")
        print(f"1. Hourly Physics Data: {hourly_path}")
        print(f"2. Monthly Financials : {monthly_path}")


        # =========================================================
        # --- 7. ADVANCED 25-YEAR BREAK-EVEN & ROI FORECAST ---
        # =========================================================

        avg_monthly_net_savings = summary_df['Net_Metering_Savings'].mean()
        base_annual_savings = avg_monthly_net_savings * 12

        # --- Variables ---
        initial_investment = 400000 + 2000    # Rs 400k system + Rs 2k CEB fee
        annual_maintenance = 3000             # Washing panels, checking wires
        panel_degradation = 0.005             # Panels lose 0.5% efficiency per year
        battery_replacement_year = 15         # Battery dies at Year 15
        battery_replacement_cost = 100000     # Cost to buy a new battery in Year 15
        project_lifespan = 25                 # We simulate over 25 years

        # --- Time-Series Simulation ---
        years = list(range(0, project_lifespan + 1))
        cumulative_cashflow = [-initial_investment]  # Year 0: We are Rs 402,000 in the hole!
        
        break_even_year = None

        for year in range(1, project_lifespan + 1):
            # Calculate this year's savings (reduced by 0.5% degradation)
            current_year_savings = base_annual_savings * ((1 - panel_degradation) ** year)
            
            # Net cash this year (Savings minus maintenance)
            net_cash_this_year = current_year_savings - annual_maintenance
            
            # Check for Battery Replacement Event!
            if year == battery_replacement_year:
                net_cash_this_year -= battery_replacement_cost
                
            # Add this year's cash to our running total
            new_cumulative_total = cumulative_cashflow[-1] + net_cash_this_year
            cumulative_cashflow.append(new_cumulative_total)
            
            # Did we break even this year?
            if cumulative_cashflow[-2] < 0 and cumulative_cashflow[-1] >= 0:
                break_even_year = year

        # --- TERMINAL REPORT ---
        total_profit = cumulative_cashflow[-1]
        
        print("\n" + "="*55)
        print(" 📈 25-YEAR FINANCIAL FORECAST 📈 ")
        print("="*55)
        print(f"Total Upfront Cost     : Rs {-cumulative_cashflow[0]:,.2f}")
        print(f"Battery Replacement    : Rs {battery_replacement_cost:,.2f} (at Year {battery_replacement_year})")
        if break_even_year:
            print(f"⏳ BREAK-EVEN YEAR     : Year {break_even_year}")
        else:
            print("🚨 SYSTEM WILL NEVER BREAK EVEN 🚨")
        print(f"💸 TOTAL NET PROFIT    : Rs {total_profit:,.2f} (After 25 years)")
        print("="*55)

        # =========================================================
        # --- 8. GENERATE THE BREAK-EVEN GRAPH ---
        # =========================================================
        plt.style.use('seaborn-v0_8-darkgrid')
        plt.figure(figsize=(10, 6))

        # Plot the cumulative cash flow line
        plt.plot(years, cumulative_cashflow, marker='o', linestyle='-', color='dodgerblue', linewidth=2)

        # Draw a thick Red line at Rs 0 (The Break-Even Line)
        plt.axhline(0, color='red', linestyle='--', linewidth=2, label="Break-Even Point (Rs 0)")

        # Highlight the battery replacement drop
        plt.annotate('Battery Replacement\n(Rs -100k)', 
                     xy=(battery_replacement_year, cumulative_cashflow[battery_replacement_year]), 
                     xytext=(battery_replacement_year-4, cumulative_cashflow[battery_replacement_year] + 50000),
                     arrowprops=dict(facecolor='orange', shrink=0.05),
                     fontsize=10, color='darkorange')

        # Formatting the Graph
        plt.title('Solar PV Investment ROI in Mauritius (25-Year Projection)', fontsize=14, fontweight='bold')
        plt.xlabel('Years Since Installation', fontsize=12)
        plt.ylabel('Cumulative Cash Flow (MUR)', fontsize=12)
        plt.xticks(np.arange(0, 26, 2))  # Tick every 2 years
        
        # Format Y-axis with commas for Rupees
        current_values = plt.gca().get_yticks()
        plt.gca().set_yticklabels(['Rs {:,.0f}'.format(x) for x in current_values])

        # Fill the area below 0 with red, and above 0 with green
        plt.fill_between(years, cumulative_cashflow, 0, where=(np.array(cumulative_cashflow) < 0), color='salmon', alpha=0.3)
        plt.fill_between(years, cumulative_cashflow, 0, where=(np.array(cumulative_cashflow) >= 0), color='lightgreen', alpha=0.5)

        plt.legend()
        plt.tight_layout()
        
        # Show the graph on screen
        plt.show()



    else:
        print("Could not retrieve data.")


