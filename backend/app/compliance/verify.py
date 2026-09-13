"""Is this specification the project's own, and is it for this system?

A project folder holds whatever the estimation team was sent, and that is not
always the project's specification: EP-30784 (Binghatti Skyblade, plot
3450398) keeps a fire alarm section whose every page is headed "BUGATTI
RESIDENCE ON PLOT 3466814". A compliance statement written against it
answers another project's clauses.

The specification says who it is for in its running header and cover. Python
settles what can be settled from that -- a plot number, the EP number, the
project's own name -- and says "unknown" for the rest. Only an unknown is
worth a model's opinion (see `assist.verify_spec`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Project
from app.services.spec_finder import SYSTEMS

from .spec_text import SpecText

# Words that name nothing in particular: every project is a "proposed
# residential building" somewhere in Dubai.
_GENERIC = {
    "proposed", "project", "projects", "building", "buildings", "residential", "residence", "residences",
    "commercial", "tower", "towers", "plot", "dubai", "sharjah", "ajman", "abu", "dhabi", "uae", "the", "and",
    "for", "with", "construction", "development", "villa", "villas", "hotel", "mall", "school", "factory",
    "floors", "floor", "roof", "basement", "podium", "podiums", "ground", "mezzanine", "phase", "district",
    "street", "road", "area", "city", "new", "office", "offices", "retail", "mixed", "use", "twin", "block",
    "specification", "specifications", "section", "system", "systems", "fire", "alarm", "detection",
}
_PLOT_RE = re.compile(r"\bPLOT\s*(?:NO\.?|NUMBER|#)?\s*[:.\-]?\s*([0-9][0-9\-/ ]{3,14}[0-9])", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"\b\d{6,8}\b")


@dataclass
class Verdict:
    project: str                 # "same" | "different" | "unknown"
    system: str                  # "same" | "different" | "unknown"
    evidence: list[str] = field(default_factory=list)
    names_in_spec: list[str] = field(default_factory=list)
    decided_by: str = "rules"    # "rules" | "ai" | "engineer"

    @property
    def settled(self) -> bool:
        return self.project != "unknown" and self.system != "unknown"

    def as_dict(self) -> dict:
        return {"project": self.project, "system": self.system, "evidence": self.evidence,
                "names_in_spec": self.names_in_spec, "decided_by": self.decided_by}


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4 and w not in _GENERIC and not w.isdigit()}


def project_verdict(project: Project, spec: SpecText) -> tuple[str, list[str], list[str]]:
    identity = spec.identity_text
    evidence: list[str] = []
    names = [line for line in spec.header_lines if not re.match(r"^\s*section\b", line, re.IGNORECASE)][:3]

    plots_in_spec = {_digits(m.group(1)) for m in _PLOT_RE.finditer(identity)}
    plots_in_spec |= set(_LONG_NUMBER_RE.findall(identity)) - {n for n in spec.section_numbers}
    plots_in_spec = {p for p in plots_in_spec if len(p) >= 6}
    own_plot = _digits(project.plot_number or "")

    if project.ep_number and re.search(rf"\bEP[-\s]?{re.escape(str(project.ep_number))}\b", identity, re.IGNORECASE):
        evidence.append(f"The specification names EP-{project.ep_number}.")
        return "same", evidence, names
    if own_plot and len(own_plot) >= 6:
        if own_plot in plots_in_spec:
            evidence.append(f"The specification names the project's plot {project.plot_number}.")
            return "same", evidence, names
        explicit = {_digits(m.group(1)) for m in _PLOT_RE.finditer(identity)}
        if explicit:
            evidence.append(
                f"The specification is headed for plot {', '.join(sorted(explicit))}; "
                f"this project is on plot {project.plot_number}."
            )
            return "different", evidence, names

    own_words = words(project.project_name) | words(project.client)
    spec_words = words(identity)
    shared = sorted(own_words & spec_words)
    if own_words and len(shared) >= max(1, min(2, len(own_words))):
        evidence.append(f"The specification names the project: {', '.join(shared)}.")
        return "same", evidence, names
    if names:
        evidence.append(f"The specification is headed \"{names[0]}\", which does not name this project.")
    else:
        evidence.append("The specification does not say which project it was written for.")
    return "unknown", evidence, names


def system_verdict(system_code: str, spec: SpecText, filename: str = "") -> tuple[str, list[str]]:
    wanted = SYSTEMS.get(system_code)
    if wanted is None:
        return "unknown", [f"Unknown system {system_code}."]
    # The title, the running header, the file's name -- and, for a
    # specification that numbers nothing, the opening of its cover ("( I )
    # FIRE DETECTION SYSTEM").
    heading_text = " ".join(filter(None, [spec.title or "", " ".join(spec.header_lines), filename,
                                          spec.cover_text[:300]]))
    numbers = set(spec.section_numbers)
    lowered = heading_text.lower()
    if numbers & set(wanted.sections) or any(k in lowered for k in wanted.keywords):
        why = next(iter(numbers & set(wanted.sections)), None)
        return "same", [f"Section {why} is a {wanted.name} section." if why else f"Titled for {wanted.name}."]
    others = [s.name for code, s in SYSTEMS.items() if code != system_code and (numbers & set(s.sections) or any(k in lowered for k in s.keywords))]
    if others:
        return "different", [f"The specification is for {', '.join(others)}, not {wanted.name}."]
    if numbers or spec.title:
        return "different", [f"Section {', '.join(sorted(numbers)) or spec.title} is not a {wanted.name} section."]
    if not spec.clauses:
        return "unknown", ["The document does not read like a specification."]
    return "unknown", ["The specification does not carry a section number or title."]


def verify(project: Project, system_code: str, spec: SpecText, filename: str = "") -> Verdict:
    project_state, project_evidence, names = project_verdict(project, spec)
    system_state, system_evidence = system_verdict(system_code, spec, filename)
    return Verdict(project_state, system_state, project_evidence + system_evidence, names)
