"""Generate a realistic synthetic pharma sales and invoicing workbook."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20261025
N_INVOICES = 10_000
N_CUSTOMERS = 2_000
N_SKUS = 865
N_REPS = 40
N_RETURNS = 800
AS_OF = pd.Timestamp("2026-01-15")
DATE_START = pd.Timestamp("2024-01-01")
DATE_END = pd.Timestamp("2025-12-31")
CURRENCY = "INR"

rng = np.random.default_rng(SEED)

REGIONS = ["North", "South", "East", "West"]
STATES = {
    "North": ["Delhi", "Haryana", "Punjab", "Rajasthan", "Uttar Pradesh"],
    "South": ["Karnataka", "Kerala", "Tamil Nadu", "Telangana", "Andhra Pradesh"],
    "East": ["West Bengal", "Odisha", "Bihar", "Jharkhand", "Assam"],
    "West": ["Maharashtra", "Gujarat", "Madhya Pradesh", "Goa", "Chhattisgarh"],
}
CITIES = {
    "North": ["Delhi", "Gurugram", "Noida", "Chandigarh", "Jaipur", "Lucknow"],
    "South": ["Bengaluru", "Hyderabad", "Chennai", "Kochi", "Coimbatore", "Visakhapatnam"],
    "East": ["Kolkata", "Bhubaneswar", "Patna", "Ranchi", "Guwahati", "Siliguri"],
    "West": ["Mumbai", "Pune", "Ahmedabad", "Indore", "Surat", "Nagpur"],
}
CUSTOMER_TYPES = ["Pharmacy", "Hospital", "Distributor", "Online", "Clinic"]
CUSTOMER_TYPE_P = [0.55, 0.15, 0.12, 0.10, 0.08]
TYPE_MEAN_QTY = {"Distributor": 24.0, "Hospital": 16.0, "Pharmacy": 7.0, "Online": 5.0, "Clinic": 4.0}
TYPE_DISCOUNT = {"Distributor": 0.12, "Hospital": 0.10, "Pharmacy": 0.05, "Online": 0.06, "Clinic": 0.03}
CHANNEL_MULT = {"Distributor": 0.93, "Hospital": 1.05, "Pharmacy": 1.0, "Online": 1.02, "Clinic": 1.0}
CREDIT_TERMS = {
    "Distributor": [30, 45, 60],
    "Hospital": [30, 45],
    "Pharmacy": [15, 30],
    "Online": [0, 7],
    "Clinic": [15, 30],
}
SEASON_FACTOR = {
    1: 0.90, 2: 0.88, 3: 1.12, 4: 0.92, 5: 0.95, 6: 1.00,
    7: 0.96, 8: 1.00, 9: 1.08, 10: 1.18, 11: 1.12, 12: 1.25,
}
PAYMENT_METHODS = ["NEFT", "UPI", "Cheque", "Credit Card", "Cash"]
RETURN_REASONS = [
    "Near expiry", "Damaged packaging", "Wrong product shipped", "Customer return",
    "Quality complaint", "Order cancelled", "Excess quantity", "Temperature excursion",
]

MOLECULES = [
    ("Paracetamol", "Analgesics", "Pain & Fever", "NSAID"),
    ("Ibuprofen", "Analgesics", "Pain & Fever", "NSAID"),
    ("Diclofenac", "Analgesics", "Pain & Fever", "NSAID"),
    ("Aspirin", "Analgesics", "Pain & Fever", "Salicylate"),
    ("Tramadol", "Analgesics", "Pain & Fever", "Opioid"),
    ("Amoxicillin", "Antibiotics", "Anti-infective", "Penicillin"),
    ("Azithromycin", "Antibiotics", "Anti-infective", "Macrolide"),
    ("Cefixime", "Antibiotics", "Anti-infective", "Cephalosporin"),
    ("Ciprofloxacin", "Antibiotics", "Anti-infective", "Fluoroquinolone"),
    ("Doxycycline", "Antibiotics", "Anti-infective", "Tetracycline"),
    ("Metformin", "Diabetes", "Metabolic", "Biguanide"),
    ("Glimepiride", "Diabetes", "Metabolic", "Sulfonylurea"),
    ("Sitagliptin", "Diabetes", "Metabolic", "DPP-4 inhibitor"),
    ("Insulin Glargine", "Diabetes", "Metabolic", "Insulin"),
    ("Empagliflozin", "Diabetes", "Metabolic", "SGLT2 inhibitor"),
    ("Amlodipine", "Cardiovascular", "Cardiac", "Calcium channel blocker"),
    ("Telmisartan", "Cardiovascular", "Cardiac", "ARB"),
    ("Losartan", "Cardiovascular", "Cardiac", "ARB"),
    ("Atorvastatin", "Cardiovascular", "Cardiac", "Statin"),
    ("Rosuvastatin", "Cardiovascular", "Cardiac", "Statin"),
    ("Metoprolol", "Cardiovascular", "Cardiac", "Beta blocker"),
    ("Ramipril", "Cardiovascular", "Cardiac", "ACE inhibitor"),
    ("Clopidogrel", "Cardiovascular", "Cardiac", "Antiplatelet"),
    ("Cetirizine", "Respiratory", "Allergy", "Antihistamine"),
    ("Levocetirizine", "Respiratory", "Allergy", "Antihistamine"),
    ("Montelukast", "Respiratory", "Asthma", "Leukotriene antagonist"),
    ("Salbutamol", "Respiratory", "Asthma", "Bronchodilator"),
    ("Budesonide", "Respiratory", "Asthma", "Corticosteroid"),
    ("Ambroxol", "Respiratory", "Cough & Cold", "Mucolytic"),
    ("Pantoprazole", "Gastro", "Acid Disorder", "PPI"),
    ("Omeprazole", "Gastro", "Acid Disorder", "PPI"),
    ("Rabeprazole", "Gastro", "Acid Disorder", "PPI"),
    ("Domperidone", "Gastro", "Motility", "Prokinetic"),
    ("Ondansetron", "Gastro", "Anti-emetic", "5-HT3 antagonist"),
    ("Lactulose", "Gastro", "Constipation", "Osmotic laxative"),
    ("Fluconazole", "Dermatology", "Fungal Infection", "Azole"),
    ("Terbinafine", "Dermatology", "Fungal Infection", "Allylamine"),
    ("Clotrimazole", "Dermatology", "Fungal Infection", "Azole"),
    ("Mometasone", "Dermatology", "Inflammation", "Corticosteroid"),
    ("Isotretinoin", "Dermatology", "Acne", "Retinoid"),
    ("Vitamin D3", "Vitamins", "Supplement", "Cholecalciferol"),
    ("Vitamin B12", "Vitamins", "Supplement", "Cyanocobalamin"),
    ("Calcium Carbonate", "Vitamins", "Supplement", "Mineral"),
    ("Iron Folic Acid", "Vitamins", "Supplement", "Mineral"),
    ("Multivitamin", "Vitamins", "Supplement", "Combination"),
    ("Timolol", "Ophthalmic", "Glaucoma", "Beta blocker"),
    ("Latanoprost", "Ophthalmic", "Glaucoma", "Prostaglandin"),
    ("Moxifloxacin Eye", "Ophthalmic", "Infection", "Fluoroquinolone"),
    ("Carboxymethylcellulose", "Ophthalmic", "Dry Eye", "Lubricant"),
    ("Olopatadine", "Ophthalmic", "Allergy", "Antihistamine"),
    ("Betahistine", "Others", "Vertigo", "Histamine analogue"),
    ("Levetiracetam", "Others", "Neurology", "Anticonvulsant"),
    ("Gabapentin", "Others", "Neurology", "Anticonvulsant"),
    ("Amitriptyline", "Others", "Neurology", "Tricyclic"),
    ("Sertraline", "Others", "Psychiatry", "SSRI"),
    ("Alprazolam", "Others", "Psychiatry", "Benzodiazepine"),
    ("Thyroxine", "Others", "Hormone", "Thyroid hormone"),
    ("Tamsulosin", "Others", "Urology", "Alpha blocker"),
    ("Sildenafil", "Others", "Urology", "PDE5 inhibitor"),
    ("Ursodeoxycholic Acid", "Others", "Hepatology", "Bile acid"),
]

STRENGTHS = ["5mg", "10mg", "20mg", "25mg", "50mg", "100mg", "125mg", "200mg", "250mg", "400mg", "500mg", "650mg", "1g"]
FORMS = {
    "Tablet": ["10s", "15s", "20s", "30s", "60s", "100s"],
    "Capsule": ["10s", "15s", "20s", "30s", "60s"],
    "Syrup": ["60ml", "100ml", "200ml"],
    "Injection": ["1 vial", "5 vials"],
    "Cream": ["15g", "30g", "50g"],
    "Ointment": ["15g", "30g", "50g"],
    "Drops": ["5ml", "10ml", "15ml"],
    "Inhaler": ["1 inhaler", "200 doses"],
    "Sachet": ["10s", "30s"],
    "Gel": ["15g", "30g"],
}
CATEGORY_BASE_PRICE = {
    "Analgesics": 85.0,
    "Antibiotics": 230.0,
    "Diabetes": 340.0,
    "Cardiovascular": 190.0,
    "Respiratory": 260.0,
    "Gastro": 165.0,
    "Dermatology": 155.0,
    "Vitamins": 210.0,
    "Ophthalmic": 195.0,
    "Others": 175.0,
}
TAX_RATE = {
    "Analgesics": 0.12,
    "Antibiotics": 0.05,
    "Diabetes": 0.12,
    "Cardiovascular": 0.12,
    "Respiratory": 0.05,
    "Gastro": 0.12,
    "Dermatology": 0.18,
    "Vitamins": 0.18,
    "Ophthalmic": 0.05,
    "Others": 0.12,
}
MANUFACTURERS = [
    "Asterwell Labs", "BluePeak Pharma", "CuraNova", "Deltacare", "Everglow Meds",
    "FreshLeaf Labs", "GreenCross Health", "Helix Remedies", "Indigo Pharma",
    "Juniper LifeSciences", "Kestrel Labs", "LumenCure", "Meridian Meds",
    "NovaSpring", "Orchid Wellness", "Pinnacle Pharma", "Quartz Labs", "Riverstone Health",
]
PHARMACY_PREFIX = ["Apollo", "MedPlus", "Wellness", "Care", "Health", "Life", "Apex", "City", "Green", "Sun", "Star", "Unity", "Nova", "Prime", "Trust", "Family", "Community", "Smart", "Quick", "Safe"]
PHARMACY_SUFFIX = ["Pharmacy", "Medical Store", "Drug House", "Chemist", "Healthcare", "Medicos"]
HOSPITAL_PREFIX = ["City", "Sunrise", "Lifeline", "Care", "Metro", "Global", "Prime", "Unity", "Hope", "Advanced"]
HOSPITAL_SUFFIX = ["Hospital", "Multispeciality Hospital", "Medical Center", "Clinic"]
DISTRIBUTOR_PREFIX = ["Medi", "Pharma", "Health", "Care", "Unity", "Apex", "Nova", "Prime", "Global", "Sun"]
DISTRIBUTOR_SUFFIX = ["Distributors", "Agencies", "Supplies", "Wholesale", "Traders"]
FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Pranav", "Rohan", "Ananya", "Diya", "Isha", "Kavya", "Meera", "Nisha", "Priya", "Riya", "Sneha", "Tanvi", "Neha", "Pooja"]
LAST_NAMES = ["Sharma", "Verma", "Patel", "Reddy", "Nair", "Iyer", "Gupta", "Mehta", "Joshi", "Kulkarni", "Rao", "Das", "Bose", "Chopra", "Malhotra", "Singh", "Yadav", "Pillai", "Menon", "Bhat"]


def _unique_names(count: int, maker) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    while len(names) < count:
        name = maker(len(names))
        if name in seen:
            name = f"{name} {len(names) + 1}"
        seen.add(name)
        names.append(name)
    return names


def build_customers() -> pd.DataFrame:
    regions = rng.choice(REGIONS, size=N_CUSTOMERS, p=[0.30, 0.28, 0.20, 0.22])
    types = rng.choice(CUSTOMER_TYPES, size=N_CUSTOMERS, p=CUSTOMER_TYPE_P)

    def make_name(idx: int) -> str:
        region = regions[idx]
        city = str(rng.choice(CITIES[region]))
        kind = types[idx]
        if kind == "Pharmacy":
            name = f"{rng.choice(PHARMACY_PREFIX)} {rng.choice(PHARMACY_SUFFIX)} - {city}"
        elif kind == "Hospital":
            name = f"{rng.choice(HOSPITAL_PREFIX)} {rng.choice(HOSPITAL_SUFFIX)} - {city}"
        elif kind == "Distributor":
            name = f"{rng.choice(DISTRIBUTOR_PREFIX)} {rng.choice(DISTRIBUTOR_SUFFIX)} - {city}"
        elif kind == "Online":
            name = f"{rng.choice(PHARMACY_PREFIX)} Online - {city}"
        else:
            name = f"{rng.choice(HOSPITAL_PREFIX)} Clinic - {city}"
        return name

    names = _unique_names(N_CUSTOMERS, make_name)
    states = [str(rng.choice(STATES[r])) for r in regions]
    cities = [str(rng.choice(CITIES[r])) for r in regions]
    terms = [int(rng.choice(CREDIT_TERMS[t])) for t in types]
    limits = []
    for t in types:
        base = {"Distributor": 1_500_000, "Hospital": 850_000, "Pharmacy": 260_000, "Online": 180_000, "Clinic": 120_000}[t]
        limits.append(int(round(rng.lognormal(mean=np.log(base), sigma=0.45) / 10_000) * 10_000))
    created = pd.to_datetime("2018-01-01") + pd.to_timedelta(rng.integers(0, 2557, size=N_CUSTOMERS), unit="D")
    emails = [f"accounts+{i:05d}@example.com" for i in range(1, N_CUSTOMERS + 1)]
    phones = [f"+91-9{rng.integers(10_000_000, 99_999_999)}" for _ in range(N_CUSTOMERS)]
    return pd.DataFrame(
        {
            "customer_id": [f"CUST-{i:05d}" for i in range(1, N_CUSTOMERS + 1)],
            "customer_name": names,
            "customer_type": types,
            "segment": [{"Pharmacy": "Retail", "Hospital": "Institutional", "Distributor": "Wholesale", "Online": "E-commerce", "Clinic": "Clinic"}[t] for t in types],
            "channel": types,
            "region": regions,
            "state": states,
            "city": cities,
            "pincode": [f"{rng.integers(110001, 799999):06d}" for _ in range(N_CUSTOMERS)],
            "gstin": [f"DEMO-GSTIN-{i:05d}" for i in range(1, N_CUSTOMERS + 1)],
            "email": emails,
            "phone": phones,
            "credit_terms_days": terms,
            "credit_limit": limits,
            "created_date": created,
            "active": rng.choice([True, False], size=N_CUSTOMERS, p=[0.97, 0.03]),
        }
    )


def build_skus() -> pd.DataFrame:
    rows: list[dict] = []
    seen: set[str] = set()
    while len(rows) < N_SKUS:
        molecule, category, area, sub_category = MOLECULES[int(rng.integers(0, len(MOLECULES)))]
        strength = str(rng.choice(STRENGTHS))
        form = str(rng.choice(list(FORMS)))
        pack = str(rng.choice(FORMS[form]))
        name = f"{molecule} {strength} {form} {pack}"
        if name in seen:
            continue
        seen.add(name)
        base_price = float(rng.lognormal(mean=np.log(CATEGORY_BASE_PRICE[category]), sigma=0.42))
        base_price = round(min(max(base_price, 20.0), 6000.0), 2)
        base_cost = round(base_price * float(rng.uniform(0.55, 0.72)), 2)
        idx = len(rows) + 1
        rows.append(
            {
                "sku": f"SKU-{idx:04d}",
                "product_name": name,
                "molecule": molecule,
                "category": category,
                "sub_category": sub_category,
                "therapeutic_area": area,
                "manufacturer": str(rng.choice(MANUFACTURERS)),
                "form": form,
                "pack_size": pack,
                "unit_of_measure": form,
                "base_price": base_price,
                "base_cost": base_cost,
                "launch_date": pd.Timestamp("2015-01-01") + pd.to_timedelta(int(rng.integers(0, 3650)), unit="D"),
                "active": bool(rng.choice([True, False], p=[0.96, 0.04])),
            }
        )
    return pd.DataFrame(rows)


def build_reps() -> pd.DataFrame:
    rows = []
    for idx in range(1, N_REPS + 1):
        region = REGIONS[(idx - 1) % len(REGIONS)]
        first = str(rng.choice(FIRST_NAMES))
        last = str(rng.choice(LAST_NAMES))
        rows.append(
            {
                "sales_rep_id": f"REP-{idx:03d}",
                "rep_name": f"{first} {last}",
                "region": region,
                "team": f"{region} Field Sales",
                "manager": f"{str(rng.choice(FIRST_NAMES))} {str(rng.choice(LAST_NAMES))}",
                "email": f"rep{idx:03d}@example.com",
                "phone": f"+91-8{rng.integers(10_000_000, 99_999_999)}",
                "hire_date": pd.Timestamp("2016-01-01") + pd.to_timedelta(int(rng.integers(0, 3285)), unit="D"),
                "active": bool(rng.choice([True, False], p=[0.95, 0.05])),
            }
        )
    return pd.DataFrame(rows)


def build_invoices(customers: pd.DataFrame, reps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    months = pd.period_range("2024-01", "2025-12", freq="M")
    base_weights = np.array([0.90, 0.88, 1.12, 0.92, 0.95, 1.00, 0.96, 1.00, 1.08, 1.18, 1.12, 1.25])
    month_weights = np.tile(base_weights, 2) * np.repeat([1.0, 1.12], 12)
    month_weights = month_weights / month_weights.sum()
    month_idx = rng.choice(len(months), size=N_INVOICES, p=month_weights)
    dates = []
    for idx in month_idx:
        period = months[int(idx)]
        day = int(rng.integers(1, period.days_in_month + 1))
        dates.append(pd.Timestamp(year=period.year, month=period.month, day=day))
    dates = sorted(dates)

    customer_ids = customers["customer_id"].to_numpy()
    customer_names = customers["customer_name"].to_numpy()
    customer_types = customers["customer_type"].to_numpy()
    customer_regions = customers["region"].to_numpy()
    customer_cities = customers["city"].to_numpy()
    customer_states = customers["state"].to_numpy()
    customer_terms = customers["credit_terms_days"].to_numpy()
    customer_weights = 0.25 + rng.lognormal(0.0, 1.0, size=N_CUSTOMERS)
    customer_weights = customer_weights / customer_weights.sum()
    customer_pick = rng.choice(N_CUSTOMERS, size=N_INVOICES, p=customer_weights)

    rep_by_region = {
        region: reps.loc[reps["region"] == region, "sales_rep_id"].to_numpy()
        for region in REGIONS
    }
    warehouse_by_region = {region: [f"WH-{region}-01", f"WH-{region}-02"] for region in REGIONS}

    invoice_rows = []
    line_rows = []
    year_counts: dict[int, int] = {}
    line_options = np.array([1, 2, 3, 4, 5, 6])
    line_probs = np.array([0.22, 0.28, 0.22, 0.14, 0.09, 0.05])
    line_probs = line_probs / line_probs.sum()

    for i, invoice_date in enumerate(dates):
        cust_idx = int(customer_pick[i])
        customer_id = str(customer_ids[cust_idx])
        customer_name = str(customer_names[cust_idx])
        customer_type = str(customer_types[cust_idx])
        region = str(customer_regions[cust_idx])
        city = str(customer_cities[cust_idx])
        state = str(customer_states[cust_idx])
        terms = int(customer_terms[cust_idx])
        channel = customer_type
        rep_id = str(rng.choice(rep_by_region[region]))
        warehouse = str(rng.choice(warehouse_by_region[region]))
        year = invoice_date.year
        year_counts[year] = year_counts.get(year, 0) + 1
        invoice_id = f"INV-{year}-{year_counts[year]:06d}"
        order_id = f"SO-{year}-{year_counts[year]:06d}"
        due_date = invoice_date + pd.Timedelta(days=terms)

        n_lines = int(rng.choice(line_options, p=line_probs))
        sku_idx = rng.choice(N_SKUS, size=n_lines, replace=False, p=sku_weights)
        season = SEASON_FACTOR[invoice_date.month]
        for line_no, si in enumerate(sku_idx, start=1):
            sku_row = sku_rows[int(si)]
            mean_qty = TYPE_MEAN_QTY[customer_type] * sku_popularity[int(si)] * season
            qty = int(max(1.0, min(5000.0, rng.gamma(shape=2.0, scale=mean_qty / 2.0))))
            unit_price = float(sku_row["base_price"]) * CHANNEL_MULT[channel] * float(rng.lognormal(0.0, 0.04))
            unit_price = round(max(unit_price, 1.0), 2)
            discount = TYPE_DISCOUNT[customer_type] + float(rng.normal(0.0, 0.025))
            if qty > 100:
                discount += 0.02
            if qty > 500:
                discount += 0.02
            discount = round(float(np.clip(discount, 0.0, 0.28)), 4)
            gross_sales = round(qty * unit_price, 2)
            discount_amount = round(gross_sales * discount, 2)
            net_sales = round(gross_sales - discount_amount, 2)
            tax_rate = TAX_RATE[str(sku_row["category"])]
            tax_amount = round(net_sales * tax_rate, 2)
            line_total = round(net_sales + tax_amount, 2)
            unit_cost = float(sku_row["base_cost"])
            cogs = round(qty * unit_cost, 2)
            gross_profit = round(net_sales - cogs, 2)
            gross_margin = round(gross_profit / net_sales, 4) if net_sales else 0.0
            expiry_days = int(rng.integers(180, 1095))
            expiry_date = invoice_date + pd.Timedelta(days=expiry_days)
            batch_no = f"B{invoice_date.year}{invoice_date.month:02d}{int(rng.integers(1000, 9999))}"
            line_rows.append(
                {
                    "invoice_id": invoice_id,
                    "line_no": line_no,
                    "invoice_date": invoice_date,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "customer_type": customer_type,
                    "region": region,
                    "state": state,
                    "city": city,
                    "channel": channel,
                    "sales_rep_id": rep_id,
                    "warehouse": warehouse,
                    "sku": str(sku_row["sku"]),
                    "product_name": str(sku_row["product_name"]),
                    "category": str(sku_row["category"]),
                    "quantity": qty,
                    "unit_price": unit_price,
                    "gross_sales": gross_sales,
                    "discount_pct": discount,
                    "discount_amount": discount_amount,
                    "net_sales": net_sales,
                    "tax_rate": tax_rate,
                    "tax_amount": tax_amount,
                    "line_total": line_total,
                    "unit_cost": unit_cost,
                    "cogs": cogs,
                    "gross_profit": gross_profit,
                    "gross_margin_pct": gross_margin,
                    "batch_no": batch_no,
                    "expiry_date": expiry_date,
                }
            )

        invoice_rows.append(
            {
                "invoice_id": invoice_id,
                "invoice_date": invoice_date,
                "order_id": order_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_type": customer_type,
                "region": region,
                "state": state,
                "city": city,
                "channel": channel,
                "sales_rep_id": rep_id,
                "warehouse": warehouse,
                "credit_terms_days": terms,
                "due_date": due_date,
                "currency": CURRENCY,
            }
        )

    lines = pd.DataFrame(line_rows)
    invoices = pd.DataFrame(invoice_rows)
    agg = (
        lines.groupby("invoice_id", as_index=False)
        .agg(
            total_lines=("line_no", "count"),
            total_quantity=("quantity", "sum"),
            subtotal=("net_sales", "sum"),
            discount_total=("discount_amount", "sum"),
            tax_total=("tax_amount", "sum"),
            cogs_total=("cogs", "sum"),
            gross_profit=("gross_profit", "sum"),
        )
    )
    invoices = invoices.merge(agg, on="invoice_id", how="left")
    invoices["subtotal"] = invoices["subtotal"].round(2)
    invoices["discount_total"] = invoices["discount_total"].round(2)
    invoices["tax_total"] = invoices["tax_total"].round(2)
    invoices["cogs_total"] = invoices["cogs_total"].round(2)
    invoices["gross_profit"] = invoices["gross_profit"].round(2)
    invoices["gross_margin_pct"] = (invoices["gross_profit"] / invoices["subtotal"]).round(4)
    invoices["freight"] = rng.choice([0.0, 250.0, 500.0, 750.0, 1200.0], size=len(invoices), p=[0.65, 0.12, 0.10, 0.08, 0.05])
    pre_round = invoices["subtotal"] + invoices["tax_total"] + invoices["freight"]
    invoices["round_off"] = (np.round(pre_round, 0) - pre_round).round(2)
    invoices["invoice_total"] = (pre_round + invoices["round_off"]).round(2)

    statuses = []
    payment_dates = []
    payment_methods = []
    amount_paid = []
    balance_due = []
    for i, row in invoices.iterrows():
        total = float(row["invoice_total"])
        due = row["due_date"]
        inv_date = row["invoice_date"]
        if due > AS_OF:
            status = str(rng.choice(["Pending", "Paid", "Partially Paid"], p=[0.82, 0.12, 0.06]))
        else:
            status = str(rng.choice(["Paid", "Partially Paid", "Pending", "Overdue"], p=[0.82, 0.08, 0.05, 0.05]))
        if status == "Paid":
            delay = int(rng.gamma(shape=2.5, scale=max(2.0, float(row["credit_terms_days"]) / 4.0)))
            pay_date = inv_date + pd.Timedelta(days=min(delay, 120))
            if pay_date > AS_OF:
                pay_date = AS_OF
            paid = total
            balance = 0.0
            method = str(rng.choice(PAYMENT_METHODS))
        elif status == "Partially Paid":
            paid = round(total * float(rng.uniform(0.30, 0.80)), 2)
            delay = int(rng.integers(3, max(4, int(row["credit_terms_days"]) + 1)))
            pay_date = inv_date + pd.Timedelta(days=delay)
            if pay_date > AS_OF:
                pay_date = AS_OF
            balance = round(total - paid, 2)
            method = str(rng.choice(PAYMENT_METHODS))
        else:
            paid = 0.0
            balance = total
            pay_date = pd.NaT
            method = ""
        statuses.append(status)
        payment_dates.append(pay_date)
        payment_methods.append(method)
        amount_paid.append(paid)
        balance_due.append(balance)

    invoices["payment_status"] = statuses
    invoices["payment_date"] = pd.to_datetime(payment_dates)
    invoices["payment_method"] = payment_methods
    invoices["amount_paid"] = np.round(amount_paid, 2)
    invoices["balance_due"] = np.round(balance_due, 2)
    invoices = invoices[
        [
            "invoice_id", "invoice_date", "order_id", "customer_id", "customer_name",
            "customer_type", "region", "state", "city", "channel", "sales_rep_id",
            "warehouse", "credit_terms_days", "due_date", "total_lines", "total_quantity",
            "subtotal", "discount_total", "tax_total", "freight", "round_off",
            "invoice_total", "cogs_total", "gross_profit", "gross_margin_pct",
            "payment_status", "payment_date", "payment_method", "amount_paid",
            "balance_due", "currency",
        ]
    ]
    return invoices, lines


def build_returns(lines: pd.DataFrame) -> pd.DataFrame:
    sample = lines.sample(n=N_RETURNS, random_state=SEED).reset_index(drop=True)
    rows = []
    for i, row in sample.iterrows():
        max_qty = max(1, min(10, int(row["quantity"])))
        qty_returned = int(rng.integers(1, max_qty + 1))
        unit_net = float(row["net_sales"]) / max(1, int(row["quantity"]))
        refund = round(unit_net * qty_returned * 0.9, 2)
        return_date = row["invoice_date"] + pd.Timedelta(days=int(rng.integers(2, 61)))
        if return_date > DATE_END:
            return_date = DATE_END
        rows.append(
            {
                "credit_note_id": f"CN-{return_date.year}-{i + 1:05d}",
                "invoice_id": row["invoice_id"],
                "return_date": return_date,
                "customer_id": row["customer_id"],
                "customer_name": row["customer_name"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "quantity_returned": qty_returned,
                "reason": str(rng.choice(RETURN_REASONS)),
                "refund_amount": refund,
                "status": str(rng.choice(["Approved", "Pending", "Rejected"], p=[0.80, 0.15, 0.05])),
            }
        )
    return pd.DataFrame(rows)


def build_targets(invoices: pd.DataFrame) -> pd.DataFrame:
    invoices = invoices.copy()
    invoices["month"] = invoices["invoice_date"].dt.to_period("M").astype(str)
    actual = (
        invoices.groupby(["month", "region"], as_index=False)
        .agg(actual_revenue=("subtotal", "sum"), actual_units=("total_quantity", "sum"))
    )
    actual["actual_revenue"] = actual["actual_revenue"].round(2)
    actual["target_revenue"] = (actual["actual_revenue"] * rng.uniform(0.85, 1.15, size=len(actual))).round(2)
    actual["target_units"] = (actual["actual_units"] * rng.uniform(0.90, 1.10, size=len(actual))).round(0).astype(int)
    actual["achievement_pct"] = (actual["actual_revenue"] / actual["target_revenue"]).round(4)
    return actual[["month", "region", "target_revenue", "target_units", "actual_revenue", "actual_units", "achievement_pct"]]


def build_readme(invoices: pd.DataFrame, lines: pd.DataFrame, customers: pd.DataFrame, skus: pd.DataFrame, reps: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    cells = sum(len(df) * len(df.columns) for df in [invoices, lines, customers, skus, reps, returns])
    rows = [
        ("Title", "Synthetic Pharma Sales & Invoicing Dataset"),
        ("Description", "Dummy data for Excel AI Agent scale, retrieval, and insight testing. No real people or companies."),
        ("Generated", "2026-10-25"),
        ("Seed", str(SEED)),
        ("Date range", f"{DATE_START.date()} to {DATE_END.date()}"),
        ("Currency", CURRENCY),
        ("Invoices", f"{len(invoices):,}"),
        ("Invoice lines", f"{len(lines):,}"),
        ("Customers", f"{len(customers):,}"),
        ("SKUs", f"{len(skus):,}"),
        ("Sales reps", f"{len(reps):,}"),
        ("Returns / credit notes", f"{len(returns):,}"),
        ("Approx data cells", f"{cells:,}"),
        ("Revenue definition", "subtotal = net sales after line discount, excluding tax and freight"),
        ("Gross profit definition", "net sales minus cost of goods sold (COGS)"),
        ("Invoice total definition", "subtotal + tax + freight + round-off"),
        ("Notes", "Dates, quantities, prices, discounts, payment status, returns, and targets are generated with seeded statistical distributions. Invoice and line totals are internally consistent; returns are separate credit notes and are not netted from invoice revenue."),
        ("Sheets", "InvoiceLines (fact, active), Invoices, Customers, SKUs, SalesReps, Returns, MonthlyTargets, README"),
        ("Suggested tests", "Top categories by net sales; monthly revenue trend; top customers; regional performance; payment aging; gross margin by category; return reasons; target achievement."),
    ]
    return pd.DataFrame(rows, columns=["Item", "Value"])


customers_df = build_customers()
skus_df = build_skus()
reps_df = build_reps()
sku_rows = skus_df.to_dict(orient="records")
sku_popularity = rng.lognormal(0.0, 0.55, size=N_SKUS)
sku_popularity = sku_popularity / sku_popularity.mean()
sku_weights = sku_popularity / sku_popularity.sum()
invoices_df, lines_df = build_invoices(customers_df, reps_df)
returns_df = build_returns(lines_df)
targets_df = build_targets(invoices_df)
readme_df = build_readme(invoices_df, lines_df, customers_df, skus_df, reps_df, returns_df)

out_dir = Path("/home/usd/excel-agent-v2/test_data")
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "Sales_Invoice_Dataset_2024_2025.xlsx"

with pd.ExcelWriter(out_path, engine="openpyxl", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
    lines_df.to_excel(writer, sheet_name="InvoiceLines", index=False)
    invoices_df.to_excel(writer, sheet_name="Invoices", index=False)
    customers_df.to_excel(writer, sheet_name="Customers", index=False)
    skus_df.to_excel(writer, sheet_name="SKUs", index=False)
    reps_df.to_excel(writer, sheet_name="SalesReps", index=False)
    returns_df.to_excel(writer, sheet_name="Returns", index=False)
    targets_df.to_excel(writer, sheet_name="MonthlyTargets", index=False)
    readme_df.to_excel(writer, sheet_name="README", index=False)

print(f"Wrote {out_path}")
print(f"Size: {out_path.stat().st_size / 1024 / 1024:.2f} MB")
print(f"Invoices: {len(invoices_df):,} | Lines: {len(lines_df):,} | Customers: {len(customers_df):,} | SKUs: {len(skus_df):,} | Returns: {len(returns_df):,}")
print(f"Net sales: {lines_df['net_sales'].sum():,.2f} {CURRENCY}")
print(f"Invoice total: {invoices_df['invoice_total'].sum():,.2f} {CURRENCY}")
print(f"Avg invoice: {invoices_df['invoice_total'].mean():,.2f} | Avg line: {lines_df['line_total'].mean():,.2f}")
print(f"Gross margin: {lines_df['gross_profit'].sum() / lines_df['net_sales'].sum():.2%}")
print("Payment status:")
print(invoices_df["payment_status"].value_counts(normalize=True).round(4).to_string())
print("Top categories by net sales:")
print(lines_df.groupby("category")["net_sales"].sum().sort_values(ascending=False).round(2).to_string())
