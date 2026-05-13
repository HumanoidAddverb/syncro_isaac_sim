import pandas as pd

# parquet_path = "/path/to/file.parquet"

# parquet_path = "/home/dexterity/vla_dataset/syncro_5/syncro_sim_1778583944/data/chunk-000/file-000.parquet"

parquet_path = "/home/dexterity/valid_dataset/file-000.parquet"

columns = pd.read_parquet(parquet_path).columns.tolist()
print(columns)