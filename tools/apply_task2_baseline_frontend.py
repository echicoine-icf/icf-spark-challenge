"""Make the current SPARK frontend Task-2-baseline focused.

Idempotent, intentionally narrow edits:
- remove the redundant NCBI search link,
- remove external API/resource-retrieval UI from the selected-record workflow,
- keep ordinary URLs in source descriptions clickable,
- label required-characteristic evidence more clearly,
- remove the obsolete 100-dataset subset warning.

Run from the repository root after extracting this patch.
"""
from __future__ import annotations

from pathlib import Path
import re

root = Path.cwd()
assert (root / "compose.chat.yaml").exists(), "Run from SPARK repository root"
path = root / "frontend/src/ChatApp.tsx"
assert path.exists(), "frontend/src/ChatApp.tsx not found"
s = path.read_text(encoding="utf-8")

# Preserve SourceLinks but drop the generic resource inspector from Study Explorer.
s = s.replace("import ResourceInspector, { SourceLinks } from './ResourceInspector';",
              "import { SourceLinks } from './ResourceInspector';")
s = re.sub(r"^import \{ sourceUrls \} from './resourceCache';\s*\n", "", s, flags=re.M)
s = re.sub(r"^import \{ isSupportedResourceLink \} from './ResourceInspector';\s*\n", "", s, flags=re.M)

# Explicitly remove the redundant NCBI search-out button, whatever text the
# earlier UI revision used inside that anchor.
s, ncbi_count = re.subn(
    r'<a\s+className="outline-button ncbi-external"[\s\S]*?</a>',
    "", s, count=1,
)

# Dataset cards now navigate to the selected dbGaP dataset record only.
resource_status = re.compile(r'<div className="dataset-resource-status">[\s\S]*?</div>')
replacement = '''<div className="dataset-resource-status">
                <span>Original dbGaP dataset metadata</span>
                <button className="dataset-follow-up" type="button" onClick={() => inspectDataset(dataset)}>View dataset record <Icon name="arrow" size={15} /></button>
              </div>'''
s = resource_status.sub(replacement, s)

# Remove the in-record external retrieval surface. JSON/XML/Raw record tabs stay.
s = re.sub(r'\s*<div className="unified-resource-area">[\s\S]*?</div>', "", s)

s = s.replace(
    "Dataset counts and visualizations reflect the returned subset (up to 100 per type), not necessarily complete study totals. Source descriptions are not validated clinical concept assignments.",
    "Dataset counts and visualizations reflect the original dataset records imported for this study from the fixed challenge snapshot. Source descriptions are not validated clinical concept assignments.",
)

s = s.replace(
    '<div className="unified-explorer-intro"><strong>Datasets & linked research resources</strong><p>Select a dataset to view only its source metadata and any URIs embedded in that dataset record.</p></div>',
    '<div className="unified-explorer-intro"><strong>Study datasets</strong><p>Select a dataset to inspect its original dbGaP metadata within this study.</p></div>',
)

s = s.replace(
    "  required_terms: string[];\n  candidate_pool_count: number;",
    "  required_terms: string[];\n  excluded_terms?: string[];\n  candidate_pool_count: number;",
)
s = s.replace(
    "{message.data.search.candidate_pool_truncated && <span className=\"truncated\"> · Candidate pool capped</span>}",
    "{message.data.search.excluded_terms?.length ? <span> · Excluding: {message.data.search.excluded_terms.join(' + ')}</span> : null}{message.data.search.candidate_pool_truncated && <span className=\"truncated\"> · Candidate pool capped</span>}",
)

# New Task 2 baseline responses include semantic roles on each required characteristic.
s = s.replace(
    "required_evidence?: Array<{ term: string; matched_sources: string[] }>;",
    "required_evidence?: Array<{ term: string; role?: string; matched_sources: string[] }>;",
)
s = s.replace(
    "{`Matched: ${e.term}`}",
    "{`Matched: ${e.role ? `${e.role} · ` : ''}${e.term}`}",
)
s = s.replace(
    "<span>{e.term}</span><small>{e.matched_sources.map(source => source.replace(/_/g, ' ')).join(' · ')}</small>",
    "<span>{e.role ? `${e.role.replace(/_/g, ' ')}: ` : ''}{e.term}</span><small>{e.matched_sources.map(source => source.replace(/_/g, ' ')).join(' · ')}</small>",
)
s = s.replace("<strong>Matched original sources</strong>", "<strong>Required characteristics · original source evidence</strong>")

# Do not leave dead imports after removing the inspector/retrieval controls.
if "<ResourceInspector" in s or "isSupportedResourceLink" in s or "sourceUrls(" in s:
    raise RuntimeError("Frontend structure changed unexpectedly; generic resource UI references remain. Share current ChatApp.tsx before applying further edits.")
if "ncbi-external" in s or "Find study at NCBI" in s or "View original dbGaP record" in s:
    raise RuntimeError("NCBI link removal did not complete")

path.write_text(s, encoding="utf-8")

dockerfile = root / "frontend/Dockerfile.chat"
if dockerfile.exists():
    docker_text = dockerfile.read_text(encoding="utf-8")
    docker_text = docker_text.replace("COPY package.json ./", "COPY package.json package-lock.json ./")
    docker_text = docker_text.replace("RUN npm install --no-audit --no-fund", "RUN npm ci --no-audit --no-fund")
    dockerfile.write_text(docker_text, encoding="utf-8")

env = root / ".env.example"
if env.exists():
    env_text = env.read_text(encoding="utf-8")
    additions = []
    if "SPARK_MODEL_SEED=" not in env_text:
        additions.append("SPARK_MODEL_SEED=42")
    if "SPARK_ENABLE_RESOURCE_EXPLORER=" not in env_text:
        additions.append("SPARK_ENABLE_RESOURCE_EXPLORER=0")
    if additions:
        env.write_text(env_text.rstrip() + "\n\n# Task 2 reproducibility / zero-egress baseline\n" + "\n".join(additions) + "\n", encoding="utf-8")

print("Task 2 frontend cleanup applied; NCBI link and external retrieval UI removed.")
print("NCBI anchors removed:", ncbi_count)
