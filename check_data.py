import joblib
scalers = joblib.load('model/scalers.joblib')
sc = scalers.get('scalers', scalers)
print('scaler keys 샘플:', list(sc.keys())[:5])
print('1001602|wl 있나?:', '1001602|wl' in sc)
print('전체 key 개수:', len(sc))