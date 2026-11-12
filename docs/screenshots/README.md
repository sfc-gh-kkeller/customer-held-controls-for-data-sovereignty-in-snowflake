# Screenshots

One or more screenshots per option, captured from the demo account and the local demo apps. Names, IBANs,
national IDs, individual salaries, account identifiers, endpoint URLs, QR codes and keys are blurred or replaced
before capture. The SQL screens are real `snow sql` output from the demo account, rendered as a terminal page with
the same redaction.

| Option | Screenshot | Shows |
| --- | --- | --- |
| 1 Tunnel | [opt1-01-tunnel](opt1-01-tunnel.png) | Tunnel closed (0 rows), opened by the owner, live join, closed again |
| 1b Notebook + DuckDB | [opt1b-01-bundle](opt1b-01-bundle.png) | Code Bundle in SPCS; only timings are written back |
| 2 Key broker | [opt2-01-stepup](opt2-01-stepup.png) | Step-up portal (TOTP + justification) |
| 2 Key broker | [opt2-02-sql](opt2-02-sql.png) | Masked, denied, then decrypted after step-up for one classification |
| 2b EAI key fetch | [opt2b-01-memo](opt2b-01-memo.png) | Memo lives for one query, not across queries |
| 2c Device flow | [opt2c-01-qr](opt2c-01-qr.png) | QR code and user code |
| 2c Device flow | [opt2c-02-approve](opt2c-02-approve.png) | Approval page on the phone |
| 2c Device flow | [opt2c-03-sql](opt2c-03-sql.png) | NULL without a key, decrypt with the session key, scope and single use |
| 2d Remote decrypt | [opt2d-01-remote](opt2d-01-remote.png) | Decrypted in AWS; strict variant raises |
| 3 Local join | [opt3-01-local-join](opt3-01-local-join.png) | Local join on-prem (rows blurred) |
| 3 Local join | [opt3-02-snowflake-saw](opt3-02-snowflake-saw.png) | Snowflake's query history: no sensitive data |
| 4 Client decrypt | [opt4-01-signin](opt4-01-signin.png) | App sign-in |
| 4 Client decrypt | [opt4-02-connect](opt4-02-connect.png) | Connect and decrypt into the local cache |
| 4 Client decrypt | [opt4-03-query](opt4-03-query.png) | Local query on decrypted data (rows blurred) |
| 4 Client decrypt | [opt4-04-compare](opt4-04-compare.png) | Same query in Snowflake fails on ciphertext |
| ★ Homomorphic | [he-01-ciphertext](he-01-ciphertext.png) | What Snowflake holds |
| ★ Homomorphic | [he-02-groupby](he-02-groupby.png) | GROUP BY on ciphertext, verified against plaintext |
| + Synthetic | [synthetic-01-scorecard](synthetic-01-scorecard.png) | Insight and privacy scorecard |
