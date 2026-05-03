import json
import os
from pathlib import Path

current_dir = Path(__file__).parent
tariff_file = current_dir / "cebTariff" / "tariffs.json"

with open(tariff_file,'r') as f:
    normal_tariff = json.load(f)

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

if __name__ == "__main__":
    units_used = 339
    my_tariff = "120"
    final_bill = calculate_no_solar_bill(units_used, my_tariff)

    print(f"For {units_used} units on Tariff {my_tariff}:")
    print(f"Your final CEB bill is: Rs {final_bill:.2f}")
    
    




