# Logs

Append-only daily activity logs. One file per day, timestamped entries.

The filename and month folder are keyed by the log's subject day (`date`), not
by the physical file creation timestamp, so backfilled logs keep the day they
describe.

Included in the template vault.

## Install

```
_Config/Taxonomy/Temporal/logs.md        ← taxonomy.md
_Config/Templates/Temporal/Logs.md       ← template.md
_Temporal/Logs/                          ← create folder
```

### Router trigger (optional)

```
- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]
```
