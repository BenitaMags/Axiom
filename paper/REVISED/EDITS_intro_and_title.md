# Edits for `main.tex` (title, abstract) and `intro.tex`

These are the places I could not replace wholesale, because those files exist
only in your Overleaf. Each is a find-and-replace. The first two are the ones
that matter most: they are false statements about what the system does, not
merely stale numbers.

---

## A. TITLE — `main.tex` — **false as written**

Current:

> Proteus: Constructing Semantically Equivalent Program Clones using LLMs and
> **Execution Traces**

No execution traces are used anywhere in this work. The phrase belongs to the
binary-detector integration that was never built. Replace with:

> **Proteus: Constructing Semantically Equivalent Program Clones with LLM Agents
> and Executable Verification**

("Executable verification" is accurate: the oracle runs golden vectors,
invariants, the test suite, and the factory path.)

---

## B. INTRODUCTION, paragraph beginning "To this end, we present Proteus"
`intro.tex` — **false as written**

Current:

> "Above these per-agent loops, *Proteus* applies a global equivalence
> oracle—our prior binary clone detector [5]—that either confirms the clone is
> equivalent to the original or localizes the divergence for targeted repair."

Replace with:

> Above these per-agent loops, \sys applies a global equivalence oracle that
> either certifies the clone as equivalent to the original or names the layer
> at which it diverges. The oracle is source-level and has five layers: import
> resolution and confirmed absence of the outgoing distribution, recorded
> golden vectors replayed at zero tolerance, program invariants swept over
> ranges, the subject's own test suite, and a direct call into the factory
> function that constructs the substituted library---a path the suite never
> reaches, because every subject injects its client.

---

## C. CONTRIBUTIONS LIST, third bullet — `intro.tex` — **false as written**

Current:

> "We develop a prototype that reuses our prior binary clone detector as an
> equivalence oracle, closing the loop between clone generation and clone
> detection."

This was never implemented. Replace with:

> \item We develop a prototype and a five-layer executable equivalence oracle,
> including a layer that executes the substituted code path directly---which
> we added after finding that dependency-injected test suites cannot reach the
> code a migration changes.

---

## D. CONTRIBUTIONS LIST, fourth bullet — `intro.tex`

Current bullet promises success, verification cost and divergence. Extend it so
the paper's actual finding is announced in the introduction rather than
arriving as a surprise in Section~V:

> \item We evaluate \sys on industrial measurement and control software against
> three configurations, and report a largely negative result: a single-shot
> baseline outperforms \sys on migration success, the verification tier does
> not separate \sys from its own ablation, and roughly half of every
> configuration's accepted clones carry serious defects that its test suite
> does not detect.

---

## E. ABSTRACT — `main.tex`

Replace the sentences from *"A blind review of all 108 generated clones..."*
through *"...carries the effect."* with:

> A blind review of all 144 generated clones by an independent model finds that
> roughly half of every configuration's accepted migrations carry serious
> behavioral defects (52--59\%, all pairwise $p>0.75$), each of them compiling
> and passing its full test suite. On migration success the single-shot
> baseline outperforms \sys (32/36 against 23/36, $p=0.025$), and the
> verification tier does not separate \sys from its own ablation (23/36 against
> 24/36, $p=1.00$); \sys leads only on structural divergence (0.207 against
> 0.141). Adding a single evidence-fed repair call to one prompt is the
> strongest configuration we tested, migrating on 34 of 36 runs at a sixth of
> the cost. These results suggest that test-suite passage is a weak correctness
> signal for clone generation, and that the value of verification here lies in
> the evidence it produces rather than in the agent architecture that consumes
> it.

Also change **"against an unverified single-shot baseline and an ablation
without the verification tier"** to **"against two single-shot baselines and an
ablation without the verification tier"**, and **"fail to separate the three
configurations"** to **"barely separate the four configurations"**.

---

## F. RELATED WORK, Section VII-A — `related.tex`

Current:

> "...and our own prior work builds an LLM-plus-emulator detector on this
> foundation [5]. Unlike all of these, *Proteus* **constructs** a clone of a
> given program; we then repurpose a detector [5] as a certification oracle,
> using recognition in service of generation."

The second clause describes something not done. Replace from "we then
repurpose" with:

> ...\sys \emph{constructs} a clone of a given program and certifies it with an
> executable source-level oracle. Repurposing a detector such as~\cite{agadakos2025bcd}
> as that oracle---using recognition in service of generation---remains future
> work.

---

## G. DISCUSSION, "Oracle-bounded correctness" — `results.tex`
*(already applied in the replacement `results.tex` I gave you)*

The sentence "it found defects on every configuration that the automated
evidence had accepted" was true but weak. It now reads: "It found serious
defects in roughly half of every configuration's accepted clones, which is the
sharpest available statement of how far the automated evidence falls short of
the property it is taken to certify."

---

## H. Reference [5] — `biblio.bib`

`agadakos2025bcd` is cited as the global oracle in the intro and implementation.
After these edits it is cited only as prior work and as future work, which is
accurate. Keep the citation; do not keep any sentence that says it is used.

---

## Nothing needs to change in

- `background.tex` — the clone taxonomy and agentic-LLM framing are unaffected.
- `approach.tex` — check only that it does not name the binary detector as the
  global oracle; if it does, apply edit B's wording.
- `evaluation_programs.tex` — the corpus table is unchanged.
