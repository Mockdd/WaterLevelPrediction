import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent
df = pd.read_csv(ROOT / "metadata_outputs" / "obsFinalStreamReg.csv", index_col=0)

df["watershedCode"] = df["codeWatershed"].astype(str).str[:4]
target_codes = ["1001", "1002", "1003"]
df_target = df[df["watershedCode"].isin(target_codes)].drop(columns=["watershedCode"])

print(f"총 {len(df_target)}개 관측소 추출")
print(df_target[["korObs", "codeWatershed"]].to_string())

out_path = ROOT / "metadata_outputs" / "obsTarget.csv"
df_target.to_csv(out_path)
print(f"저장 완료: {out_path}")
