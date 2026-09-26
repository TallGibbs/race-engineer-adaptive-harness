# Inside the Harness

Editable sources for the interactive Experiment 1 to Experiment 2 walkthrough.

[Open the published walkthrough](https://tallgibbs.github.io/race-engineer-adaptive-harness/experiment-walkthrough/).

## Build

From the repository root, using Python and Git:

```sh
python presentations/experiment-walkthrough/build_data.py
node --check presentations/experiment-walkthrough/app.js
```

The builder combines `page.html`, `style.css`, and `app.js` with the public
experiment reports and selected code sources. It validates the report tables
and writes `docs/experiment-walkthrough/index.html`, a self-contained file
that works offline and is served by GitHub Pages. Node is only needed for the
optional JavaScript syntax check.

Evidence is pinned to commit `059e075b1eb6467b5acf871b77e431f934f9bf30` so the
historical results and source links remain stable as the repository changes.
A shallow clone must contain that commit; fetch it if necessary before building:

```sh
git fetch origin 059e075b1eb6467b5acf871b77e431f934f9bf30
```

The build does not run the harness, connect to MongoDB, or call any model.
After an edit, commit both the authoring changes and the regenerated HTML.

## Presentation controls

- Use Next, Back, or the arrow keys to move through 11 chapters.
- Play story advances every 18 seconds and pauses when evidence is opened.
- Present uses a compact layout and requests fullscreen.
- Results compares both experiments, with filters and all 36 run IDs.
- MongoDB explains the six collections with report-backed field views.
- Evidence provides original source excerpts and a local trace importer.

## Full conversation records

The public reports include results, lesson and proposal text, and event
references. They do not include the complete agent conversations. The site
labels workflow explanations and quotes only text present in those reports.

Use the original harness Python environment, with `pymongo` and
`python-dotenv`, to make a local read-only export:

```sh
python presentations/experiment-walkthrough/export_mongodb.py --env .env --out presentations/experiment-walkthrough/experiments.json
```

The helper reads exp1/exp2 runs, their events and evaluations, the experiment
records, referenced lessons, and versions v1 through v3. It never invokes a
model, creates indexes, or writes to MongoDB. Known credentials are checked
before writing the export; embedding vectors are omitted.

In the site, open Evidence and load the JSON file. Then open a run from
Results to inspect its messages, model calls, tool results, and context
manifests. The file stays in the browser tab and is not uploaded.

Keep exports local. JSON exports in this source folder are gitignored; do
not place them under `docs/` or force-add them to Git.
