import argparse
from pathlib import Path
import pandas as pd


def filter_columns(df, groups):
    """
    Filter dataframe columns based on requested groups.
    Example:
        --columns state action 
    """

    selected_cols = []

    for group in groups:
        group = group.lower()

        if group == "state":
            selected_cols.extend([c for c in df.columns if "state" in c.lower()])

        elif group == "action":
            selected_cols.extend([c for c in df.columns if "action" in c.lower()])

        elif group == "observation":
            selected_cols.extend([c for c in df.columns if "observation" in c.lower()])

        elif group == "timestamp":
            selected_cols.extend([c for c in df.columns if "time" in c.lower()])

        else:
            # Exact column match fallback
            matched = [c for c in df.columns if c == group]
            selected_cols.extend(matched)

    # Remove duplicates while preserving order
    selected_cols = list(dict.fromkeys(selected_cols))

    return df[selected_cols]


def main():
    parser = argparse.ArgumentParser(
        description="Extract selected column groups from parquet into CSV"
    )

    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help="Dataset root directory"
    )

    parser.add_argument(
        "--columns",
        nargs="+",
        required=True,
        help=(
            "Column groups or exact column names to extract.\n"
            "Examples: state action timestamp"
        )
    )

    parser.add_argument(
        "--output",
        type=str,
        default="filtered_output.csv",
        help="Output CSV filename"
    )

    args = parser.parse_args()

    parquet_path = (
        Path(args.root)
        / "data"
        / "chunk-000"
        / "file-000.parquet"
    )

    print(f"Reading parquet: {parquet_path}")

    df = pd.read_parquet(parquet_path)

    filtered_df = filter_columns(df, args.columns)

    print("\nSelected columns:")
    for c in filtered_df.columns:
        print(f"  - {c}")

    filtered_df.to_csv(args.output, index=False)

    print(f"\nSaved CSV to: {args.output}")


if __name__ == "__main__":
    main()