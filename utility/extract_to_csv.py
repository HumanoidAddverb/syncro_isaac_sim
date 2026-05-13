import pandas as pd

# Input parquet file
parquet_path = "/home/dexterity/vla_dataset/syncro_5/syncro_sim_1778583944/data/chunk-000/file-000.parquet"

# Output CSV file
csv_output_path = "first_5_rows_ep0.csv"

# Read parquet
df = pd.read_parquet(parquet_path)

# Extract first 5 rows
df_first5 = df.head(5)

# Save to CSV
df_first5.to_csv(csv_output_path, index=False)

print(f"Saved first 5 rows to: {csv_output_path}")