# immich-photo-organiser

Scans your Immich library, clusters photos into events/trips, and suggests
album names and tags using a local Ollama model. You review the plan, then
apply it.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your Immich URL/API key and Ollama host/model.

## Usage

1. **Scan & classify** — fetches new assets, clusters them, and proposes
   albums/tags into `plan.json`:

   ```bash
   python main.py
   ```

2. **Review the plan** — open `plan.json` and for each cluster you want
   applied, set:

   ```json
   "action": "approve"   // or "delete" to trash the assets instead
   ```

3. **Execute** — applies approved actions in Immich:

   ```bash
   python execute.py
   ```

Logs are written to `logs/pipeline.log`. Both scripts are safe to re-run;
already-processed assets/clusters are skipped.
