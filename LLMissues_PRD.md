# Product Requirements Document (PRD)

## Product Name (Working)
**Issue Intelligence & Meeting Continuity System (IIMCS)**

---

## 1. Purpose & Vision

The Issue Intelligence & Meeting Continuity System (IIMCS) is an assistive intelligence application designed to maintain a continuous, structured understanding of operational and strategic issues across documents, meetings, and time.

The system ingests selected documents from a Calibre library, identifies recurring or emergent issues, reconciles them against an existing issue register, and maintains each issue using a structured **SCR (Situation–Complication–Resolution)** framework with explicit next steps.

In parallel, the system analyzes upcoming meetings to surface relevant open issues, past discussions, and new evidence, supporting better agenda formation, follow-through, and decision continuity.

**Guiding Principle**  
The system proposes; the user decides. Judgment remains human.

---

## 2. Problem Statement

Professionals operating in complex environments repeatedly encounter the same underlying issues across:
- Reading and research
- Meetings and discussions
- Informal notes and follow-ups

These issues are often:
- Rediscovered rather than tracked
- Discussed without cumulative context
- Poorly framed for decision-making
- Lost between meetings

Existing tools (note-taking applications, task managers, project trackers) do not maintain **issue-level continuity** or evolve issue understanding over time.

---

## 3. Goals & Non-Goals

### 3.1 Goals
- Maintain a persistent, evolving issue register
- Detect when new documents reinforce, contradict, or materially alter existing issues
- Structure issues using the SCR framework to support executive reasoning
- Bridge documents, meetings, and actions into a coherent flow
- Reduce repeated rediscovery of known issues
- Improve meeting effectiveness via agenda and follow-up support

### 3.2 Non-Goals
- Replace project management systems (e.g., Jira, Asana)
- Automate decisions or commitments
- Serve as a multi-user collaboration platform (v1)
- Act as a general-purpose document summarization tool

---

## 4. Target User

Primary user:
- Executive, operator, advisor, or senior manager
- Maintains a curated Calibre library
- Manages recurring, cross-functional issues
- Values transparency, provenance, and structured thinking

Assumptions:
- Single primary user in v1
- High tolerance for editing and refinement
- Preference for assistive intelligence over automation

---

## 5. Core Concepts & Definitions

### 5.1 Issue
A persistent concern, risk, constraint, or decision area that recurs across documents and meetings and requires ongoing attention.

### 5.2 SCR Framework
- **Situation:** Objective facts and current state
- **Complication:** Constraints, risks, tensions, and unknowns
- **Resolution (Hypotheses):** Candidate paths forward, not commitments

### 5.3 Issue Register
The authoritative, evolving list of all known issues and their current status.

---

## 6. Functional Requirements

### 6.1 Document Ingestion (Calibre)

**Triggering Conditions**
- Documents added to Calibre with user-defined tags
- Manual reprocessing initiated by the user

**System Behavior**
- Ingest document metadata and full text
- Extract key themes, risks, decisions, and tensions
- Generate candidate issue statements

**Constraints**
- Ingestion limited to tagged documents
- Processing may be asynchronous

---

### 6.2 Issue Detection & Matching

**Matching Logic**
- Compare candidate issue statements against open issues using semantic similarity and domain alignment

**Classification Outcomes**
- **Existing Issue Match**
  - Update evidence and context
  - Revise SCR components as needed
- **Possible Match**
  - Flag for explicit user confirmation
- **New Issue**
  - Create a draft issue with initial SCR and confidence score

**Requirements**
- Conservative issue creation bias
- Explicit confidence scoring
- Full traceability to source documents

---

### 6.3 Issue Creation & Maintenance

**Issue Attributes**
- Unique Issue ID
- Title
- Domain / category
- Status (Open, Watching, Mitigated, Closed)
- Confidence score
- SCR sections
- Next steps
- Linked documents
- Linked meetings
- Created and last-updated timestamps

**User Capabilities**
- Edit any issue field
- Accept or reject system-proposed updates
- Merge or split issues
- Adjust confidence and status

**System Capabilities**
- Preserve full revision history
- Distinguish system-generated vs user-authored content
- Learn from accepted and rejected edits (future enhancement)

---

### 6.4 Issue Register Management

**Core Capabilities**
- Filter and sort issues by status, domain, age, or activity
- Highlight stale or repeatedly discussed issues
- Display issue lineage (documents → meetings → actions)

**Advanced Capabilities (Future)**
- Issue convergence detection
- Issue drift detection

---

### 6.5 Meeting Intelligence (Daily Review)

**Trigger**
- Daily review at a configurable time

**Inputs**
- Calendar schedule
- Past meeting notes or transcripts
- Issue register
- Recent document updates

**Outputs**
- Suggested agenda items tied to open issues
- Identification of unresolved discussions
- Indicators of decision readiness

---

### 6.6 Agenda & Follow-Up Support

**Agenda Support**
- Issue-based agenda blocks
- Framing questions per issue
- Suggested time allocation

**Follow-Up Support**
- Draft summaries
- Draft action lists
- Proposed issue status updates

**Constraint**
- Drafts only; no automatic sending or posting

---

## 7. User Experience Requirements

- Clear separation between system suggestions and user-validated content
- Inline editing for all issue components
- One-click provenance for every issue element
- Confidence and uncertainty explicitly displayed
- Minimal modal interactions

---

## 8. Non-Functional Requirements

### 8.1 Transparency
- Every issue must display source documents, similarity rationale, and revision history

### 8.2 Reliability
- No silent overwrites
- Deterministic reprocessing where feasible

### 8.3 Performance
- Daily meeting review perceived latency under 5 seconds
- Document ingestion handled asynchronously

### 8.4 Extensibility
- Pluggable document sources
- Pluggable issue frameworks (e.g., SCR now, RAID later)

---

## 9. MVP Scope

### Included
- Calibre ingestion by tag
- Issue detection and matching
- Issue register
- SCR drafting
- Manual editing
- Daily meeting agenda suggestions

### Excluded
- Multi-user support
- Automated communications
- Deep analytics dashboards
- Full task management

---

## 10. Risks & Open Questions

- Thresholds for issue creation vs update
- Merge vs split heuristics
- Handling speculative or sensitive issues
- Reprocessing historical documents
- Issue decay and archival policies

---

## 11. Success Metrics

- Reduction in repeated issue rediscovery
- Percentage of meetings using suggested agendas
- Time from issue detection to first action
- User acceptance rate of suggested updates
- Subjective trust score

---

## 12. Future Considerations

- Multi-user issue ownership
- Cross-issue dependency mapping
- Quantitative risk scoring
- Integration with planning and reporting tools
