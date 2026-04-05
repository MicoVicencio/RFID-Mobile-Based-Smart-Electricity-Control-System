import pandas as pd
import random

def generate_bulk_excel(filename="teacher_import_test.xlsx", count=200):
    # Data pools for randomization
    first_names = ["Juan", "Maria", "Ricardo", "Elena", "Antonio", "Sonia", "Pedro", "Liza", "Roberto", "Gina", 
                   "Angelo", "Beatriz", "Carlos", "Dolores", "Esteban", "Felicity", "Gregorio", "Hilda", "Ignacio", "Jules"]
    last_names = ["Dela Cruz", "Santos", "Reyes", "Pascual", "Bautista", "Garcia", "Mendoza", "Torres", "Aquino", "Lopez",
                  "Villanueva", "Castro", "Briones", "Salvador", "Guzman", "Sarmiento", "Pineda", "Valdez", "Navarro", "Soriano"]
    departments = ["Elementary", "Junior High", "Senior High", "College"]

    data = []
    unique_rfids = set()

    print(f"Generating {count} dummy teachers...")

    while len(data) < count:
        # Generate Name
        f_name = random.choice(first_names)
        l_name = random.choice(last_names)
        full_name = f"{f_name} {l_name} {len(data) + 1}" # Added number to ensure uniqueness
        
        # Generate Unique RFID
        rfid = ''.join(random.choices("0123456789ABCDEF", k=8))
        if rfid in unique_rfids:
            continue
        unique_rfids.add(rfid)

        # Generate Email and Dept
        email = f"{f_name.lower()}{len(data)}@ccc.edu.ph"
        dept = random.choice(departments)

        # Append in the EXACT order of your reference table
        data.append({
            "rfid": rfid,
            "teachername": full_name,
            "email": email,
            "department": dept
        })

    # Create DataFrame and Export
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False)
    
    print(f"Successfully created {filename} with {count} records.")

if __name__ == "__main__":
    generate_bulk_excel()