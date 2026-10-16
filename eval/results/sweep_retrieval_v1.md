# Threshold Sweep — retrieval, corpus v1

## en · concept-name=first-key-term

```
run ab704314-170d-4ce6-b4cc-ae4016ca548d  git 0aa7cd50b487+dirty  sut {'embedding_model': 'intfloat/multilingual-e5-large', 'embedding_device': 'cpu', 'vector_index': 'hnsw', 'metric': 'cosine'}

recall cut-off
   recall cut-off   gated_recall@5 (n=128)   abstention_correct (n=16)
            0.60                  0.984                      0.000
            0.62                  0.984                      0.000
            0.64                  0.984                      0.000
            0.66                  0.984                      0.000
            0.68                  0.984                      0.000
            0.70                  0.984                      0.000
            0.72                  0.984                      0.000
            0.74                  0.984                      0.000
            0.76                  0.982                      0.500
            0.78                  0.962                      0.938
            0.80                  0.868                      1.000
            0.82                  0.650                      1.000
            0.84                  0.438                      1.000  ← production
            0.86                  0.180                      1.000
            0.88                  0.078                      1.000
            0.90                  0.008                      1.000
            0.92                  0.000                      1.000
            0.94                  0.000                      1.000
            0.96                  0.000                      1.000
   lowest cut-off refusing every abstention probe: 0.787 (gated recall at 0.78: 0.962)

pair similarities (passage↔passage, TR-601)
   pair class                n     min    p10    p50    p90    max
   same (paraphrase)        240  0.941  0.972  0.984  0.993  0.999
   revision (old↔new)         8  0.878  0.879  0.897  0.921  0.921
   distinct, same domain    174  0.744  0.794  0.843  0.900  0.946
   distinct, any           1757  0.714  0.759  0.808  0.868  0.946

merge / disambiguate thresholds (TR-602)
   merge  disamb | same merged  same missed | distinct MERGED  distinct→LLM | revision merged  revision→LLM
   0.93   0.60  |       1.000        0.000 |           0.002         0.998 |           0.000         1.000
   0.93   0.65  |       1.000        0.000 |           0.002         0.998 |           0.000         1.000
   0.93   0.70  |       1.000        0.000 |           0.002         0.998 |           0.000         1.000
   0.93   0.75  |       1.000        0.000 |           0.002         0.949 |           0.000         1.000  ← production
   0.93   0.80  |       1.000        0.000 |           0.002         0.564 |           0.000         1.000
   0.93   0.85  |       1.000        0.000 |           0.002         0.211 |           0.000         1.000
   0.93   0.90  |       1.000        0.000 |           0.002         0.017 |           0.000         0.375
   0.85   0.75  |       1.000        0.000 |           0.213         0.738 |           1.000         0.000
   0.87   0.75  |       1.000        0.000 |           0.093         0.858 |           1.000         0.000
   0.89   0.75  |       1.000        0.000 |           0.032         0.919 |           0.750         0.250
   0.91   0.75  |       1.000        0.000 |           0.011         0.940 |           0.375         0.625
   0.93   0.75  |       1.000        0.000 |           0.002         0.949 |           0.000         1.000  ← production
   0.95   0.75  |       0.992        0.000 |           0.000         0.951 |           0.000         1.000
   0.97   0.75  |       0.917        0.000 |           0.000         0.951 |           0.000         1.000
   0.99   0.75  |       0.254        0.000 |           0.000         0.951 |           0.000         1.000
   lowest merge threshold fusing no distinct pair: 0.946
```

## en · concept-name=none-in-embedding

```
run be7e650f-b07e-4e68-bf2d-4f3891d78a05  git 0aa7cd50b487+dirty  sut {'embedding_model': 'intfloat/multilingual-e5-large', 'embedding_device': 'cpu', 'vector_index': 'hnsw', 'metric': 'cosine'}

recall cut-off
   recall cut-off   gated_recall@5 (n=128)   abstention_correct (n=16)
            0.60                  0.982                      0.000
            0.62                  0.982                      0.000
            0.64                  0.982                      0.000
            0.66                  0.982                      0.000
            0.68                  0.982                      0.000
            0.70                  0.982                      0.000
            0.72                  0.982                      0.000
            0.74                  0.979                      0.188
            0.76                  0.962                      0.688
            0.78                  0.910                      0.938
            0.80                  0.811                      1.000
            0.82                  0.602                      1.000
            0.84                  0.367                      1.000  ← production
            0.86                  0.172                      1.000
            0.88                  0.047                      1.000
            0.90                  0.000                      1.000
            0.92                  0.000                      1.000
            0.94                  0.000                      1.000
            0.96                  0.000                      1.000
   lowest cut-off refusing every abstention probe: 0.781 (gated recall at 0.78: 0.910)

pair similarities (passage↔passage, TR-601)
   pair class                n     min    p10    p50    p90    max
   same (paraphrase)        240  0.928  0.968  0.983  0.992  0.999
   revision (old↔new)         8  0.851  0.864  0.890  0.925  0.934
   distinct, same domain    174  0.743  0.785  0.853  0.903  0.946
   distinct, any           1757  0.692  0.759  0.809  0.870  0.946

merge / disambiguate thresholds (TR-602)
   merge  disamb | same merged  same missed | distinct MERGED  distinct→LLM | revision merged  revision→LLM
   0.93   0.60  |       0.996        0.000 |           0.002         0.998 |           0.125         0.875
   0.93   0.65  |       0.996        0.000 |           0.002         0.998 |           0.125         0.875
   0.93   0.70  |       0.996        0.000 |           0.002         0.995 |           0.125         0.875
   0.93   0.75  |       0.996        0.000 |           0.002         0.945 |           0.125         0.875  ← production
   0.93   0.80  |       0.996        0.000 |           0.002         0.557 |           0.125         0.875
   0.93   0.85  |       0.996        0.000 |           0.002         0.224 |           0.125         0.875
   0.93   0.90  |       0.996        0.000 |           0.002         0.017 |           0.125         0.250
   0.85   0.75  |       1.000        0.000 |           0.227         0.721 |           1.000         0.000
   0.87   0.75  |       1.000        0.000 |           0.098         0.849 |           0.750         0.250
   0.89   0.75  |       1.000        0.000 |           0.033         0.914 |           0.500         0.500
   0.91   0.75  |       1.000        0.000 |           0.012         0.935 |           0.250         0.750
   0.93   0.75  |       0.996        0.000 |           0.002         0.945 |           0.125         0.875  ← production
   0.95   0.75  |       0.992        0.000 |           0.000         0.947 |           0.000         1.000
   0.97   0.75  |       0.883        0.000 |           0.000         0.947 |           0.000         1.000
   0.99   0.75  |       0.192        0.000 |           0.000         0.947 |           0.000         1.000
   lowest merge threshold fusing no distinct pair: 0.946
```

## fr · concept-name=first-key-term

```
run beabf3c4-e297-4479-842a-5739a709cf8a  git 0aa7cd50b487+dirty  sut {'embedding_model': 'intfloat/multilingual-e5-large', 'embedding_device': 'cpu', 'vector_index': 'hnsw', 'metric': 'cosine'}

recall cut-off
   recall cut-off   gated_recall@5 (n=131)   abstention_correct (n=16)
            0.60                  0.993                      0.000
            0.62                  0.993                      0.000
            0.64                  0.993                      0.000
            0.66                  0.993                      0.000
            0.68                  0.993                      0.000
            0.70                  0.993                      0.000
            0.72                  0.993                      0.000
            0.74                  0.993                      0.000
            0.76                  0.993                      0.000
            0.78                  0.985                      0.188
            0.80                  0.929                      0.812
            0.82                  0.829                      1.000
            0.84                  0.626                      1.000  ← production
            0.86                  0.244                      1.000
            0.88                  0.023                      1.000
            0.90                  0.000                      1.000
            0.92                  0.000                      1.000
            0.94                  0.000                      1.000
            0.96                  0.000                      1.000
   lowest cut-off refusing every abstention probe: 0.811 (gated recall at 0.82: 0.829)

pair similarities (passage↔passage, TR-601)
   pair class                n     min    p10    p50    p90    max
   same (paraphrase)        240  0.947  0.971  0.985  0.994  0.999
   revision (old↔new)        11  0.901  0.902  0.910  0.921  0.927
   distinct, same domain    146  0.814  0.854  0.890  0.928  0.946
   distinct, any           1827  0.784  0.828  0.863  0.909  0.949

merge / disambiguate thresholds (TR-602)
   merge  disamb | same merged  same missed | distinct MERGED  distinct→LLM | revision merged  revision→LLM
   0.93   0.60  |       1.000        0.000 |           0.014         0.986 |           0.000         1.000
   0.93   0.65  |       1.000        0.000 |           0.014         0.986 |           0.000         1.000
   0.93   0.70  |       1.000        0.000 |           0.014         0.986 |           0.000         1.000
   0.93   0.75  |       1.000        0.000 |           0.014         0.986 |           0.000         1.000  ← production
   0.93   0.80  |       1.000        0.000 |           0.014         0.977 |           0.000         1.000
   0.93   0.85  |       1.000        0.000 |           0.014         0.651 |           0.000         1.000
   0.93   0.90  |       1.000        0.000 |           0.014         0.178 |           0.000         1.000
   0.85   0.75  |       1.000        0.000 |           0.666         0.334 |           1.000         0.000
   0.87   0.75  |       1.000        0.000 |           0.419         0.581 |           1.000         0.000
   0.89   0.75  |       1.000        0.000 |           0.279         0.721 |           1.000         0.000
   0.91   0.75  |       1.000        0.000 |           0.093         0.907 |           0.636         0.364
   0.93   0.75  |       1.000        0.000 |           0.014         0.986 |           0.000         1.000  ← production
   0.95   0.75  |       0.992        0.000 |           0.000         1.000 |           0.000         1.000
   0.97   0.75  |       0.912        0.000 |           0.000         1.000 |           0.000         1.000
   0.99   0.75  |       0.275        0.000 |           0.000         1.000 |           0.000         1.000
   lowest merge threshold fusing no distinct pair: 0.949
```

## fr · concept-name=none-in-embedding

```
run 45770f00-5971-4b1e-9244-0809ba420c8e  git 0aa7cd50b487+dirty  sut {'embedding_model': 'intfloat/multilingual-e5-large', 'embedding_device': 'cpu', 'vector_index': 'hnsw', 'metric': 'cosine'}

recall cut-off
   recall cut-off   gated_recall@5 (n=131)   abstention_correct (n=16)
            0.60                  0.991                      0.000
            0.62                  0.991                      0.000
            0.64                  0.991                      0.000
            0.66                  0.991                      0.000
            0.68                  0.991                      0.000
            0.70                  0.991                      0.000
            0.72                  0.991                      0.000
            0.74                  0.991                      0.000
            0.76                  0.991                      0.000
            0.78                  0.983                      0.188
            0.80                  0.921                      0.812
            0.82                  0.844                      1.000
            0.84                  0.634                      1.000  ← production
            0.86                  0.305                      1.000
            0.88                  0.031                      1.000
            0.90                  0.000                      1.000
            0.92                  0.000                      1.000
            0.94                  0.000                      1.000
            0.96                  0.000                      1.000
   lowest cut-off refusing every abstention probe: 0.807 (gated recall at 0.80: 0.921)

pair similarities (passage↔passage, TR-601)
   pair class                n     min    p10    p50    p90    max
   same (paraphrase)        240  0.928  0.968  0.983  0.993  0.998
   revision (old↔new)        11  0.887  0.891  0.906  0.934  0.957
   distinct, same domain    146  0.835  0.847  0.889  0.928  0.946
   distinct, any           1827  0.769  0.821  0.853  0.909  0.949

merge / disambiguate thresholds (TR-602)
   merge  disamb | same merged  same missed | distinct MERGED  distinct→LLM | revision merged  revision→LLM
   0.93   0.60  |       0.996        0.000 |           0.014         0.986 |           0.273         0.727
   0.93   0.65  |       0.996        0.000 |           0.014         0.986 |           0.273         0.727
   0.93   0.70  |       0.996        0.000 |           0.014         0.986 |           0.273         0.727
   0.93   0.75  |       0.996        0.000 |           0.014         0.986 |           0.273         0.727  ← production
   0.93   0.80  |       0.996        0.000 |           0.014         0.974 |           0.273         0.727
   0.93   0.85  |       0.996        0.000 |           0.014         0.524 |           0.273         0.727
   0.93   0.90  |       0.996        0.000 |           0.014         0.174 |           0.273         0.364
   0.85   0.75  |       1.000        0.000 |           0.539         0.461 |           1.000         0.000
   0.87   0.75  |       1.000        0.000 |           0.357         0.643 |           1.000         0.000
   0.89   0.75  |       1.000        0.000 |           0.262         0.738 |           0.909         0.091
   0.91   0.75  |       1.000        0.000 |           0.093         0.907 |           0.455         0.545
   0.93   0.75  |       0.996        0.000 |           0.014         0.986 |           0.273         0.727  ← production
   0.95   0.75  |       0.983        0.000 |           0.000         1.000 |           0.091         0.909
   0.97   0.75  |       0.875        0.000 |           0.000         1.000 |           0.000         1.000
   0.99   0.75  |       0.233        0.000 |           0.000         1.000 |           0.000         1.000
   lowest merge threshold fusing no distinct pair: 0.949
```
