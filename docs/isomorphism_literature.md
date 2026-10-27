# Literature: code isomorphism / semantics-preserving variants — and LLM prior work

Compiled for the DESSERT submission. Everything below was verified against the
publisher or arXiv record (authors, venue, year, DOI).

---

## 1. The closest prior work — read this one first

**Galápagos: Automated N-Version Programming with LLMs**
Javier Ron, Diogo Gaspar, Javier Cabrera-Arteaga, Benoit Baudry, Martin Monperrus
*ACM Transactions on Software Engineering and Methodology (TOSEM)*, 2025.
doi:10.1145/3785363 · https://arxiv.org/abs/2408.09536

This is the paper we most need to know about. They use LLMs to generate
program variants, validate correctness *and equivalence*, and assemble them
into N-Version binaries. They report "systematic diversity measurement":
variants are statically different after compilation and show diverging
internal behaviour at runtime. They also demonstrate a real dependability
payoff — the variants protect C code against genuine miscompilation bugs in
Clang.

**Why it matters to us:** it establishes that LLM-generated equivalent
variants are a legitimate, publishable research direction, and it validates
the pipeline shape (generate → check equivalence → measure diversity).

**How our work differs — this is what we must be able to state clearly:**

| | Galápagos | Ours |
|---|---|---|
| Language | C | Python |
| Combination | N-Version *ensemble* (multiple binaries) | **Merge** (one file) |
| Equivalence check | formal verification | executable oracle (golden + invariants + coverage) |
| Size control | none | **user-specified target line count** |
| Paired with | nothing | **library migration** (AXIOM) |
| Domain | general C components | ICS / safety-critical control loops |

Note it is from Baudry and Monperrus's group — the same people behind the
2014 "sosies" work below. They are the leading group in this area, so their
work should be cited generously rather than worked around.

---

## 2. LLM-generated semantics-preserving transformations

**Auto-SPT: Automating Semantic Preserving Transformations for Code**
Ashish Hooda, Mihai Christodorescu, Chuangang Ren, Aaron Wilson, Kassem Fawaz,
Somesh Jha. arXiv:2512.06042, December 2025.
https://arxiv.org/abs/2512.06042

LLMs are used to craft a diverse set of Semantic Preserving Transformations
(SPTs), generate implementations for them, and **compose** them into stronger
transformations. Includes a formal analysis showing that the *diversity of the
individual SPTs determines the strength of their composition*.

**Ideas worth borrowing:** the composition result is directly useful to us —
it argues for using several different transformation classes rather than
applying one repeatedly, which is exactly the design decision we face when
hitting a length target. Their SPT taxonomy is also a ready-made catalogue.

**Different purpose:** they build SPTs to *attack* code-clone detectors and to
augment training data — adversarial ML robustness, not dependability. So the
technique overlaps; the research question does not.

**Challenging Machine Learning-based Clone Detectors via Semantic-preserving
Code Transformations** — arXiv:2111.10793
Earlier work in the same vein, useful for the transformation catalogue.

**HyClone: Bridging LLM Understanding and Dynamic Execution for Semantic Code
Clone Detection** — arXiv:2508.01357
Two-stage: LLM screening plus *execution-based* validation, for Python. The
execution-validation half is essentially the same argument we make for using
`test_corpus.py` as the oracle rather than a static similarity measure.

**Disproving Program Equivalence with LLMs** — arXiv:2502.18473
The inverse problem — using LLMs to find inputs that *distinguish* two
programs. Directly relevant to strengthening our oracle: this is a principled
way to search for a counterexample rather than trusting fixed vectors.

---

## 3. Classical foundations (pre-LLM) — cite for framing

**Tailored Source Code Transformations to Synthesize Computationally Diverse
Program Variants**
Benoit Baudry, Simon Allier, Martin Monperrus. *ISSTA 2014*.
doi:10.1145/2610384.2610415 · https://arxiv.org/abs/1401.7635

"Sosies": source-level variants with the same functionality but different
executions, validated by the original test suite, with computation diversity
then measured. 30,184 variants across 9 real applications. **The architectural
ancestor of what we are building**, and the precedent for using a test suite
as the equivalence oracle.

**SoK: Automated Software Diversity**
Per Larsen, Andrei Homescu, Stefan Brunthaler, Michael Franz.
*IEEE Symposium on Security and Privacy 2014*, pp. 276–291. doi:10.1109/SP.2014.25
The standard survey. Cite once for the moving-target-defense framing.

**Quantifying the Effectiveness of Software Diversity using Near-Duplicate
Detection Algorithms**
Joel Coffman, Aurin Chakravarty, Joshua A. Russo, Andrew S. Gearhart.
*5th ACM Workshop on Moving Target Defense (MTD@CCS)*, 2018.
doi:10.1145/3268966.3268974
Argues that despite many diversity techniques, little is known about their
effectiveness — the justification for measuring diversity at all.

---

## 4. ICS / dependability context

**A Survey of Static Formal Methods for Building Dependable Industrial
Automation Systems** — arXiv:2108.05556
Useful for motivating the domain: why correctness guarantees matter more in
ICS than in general software.

**HyPLC: Hybrid Programmable Logic Controller Program Translation for
Verification** — arXiv:1902.05205
Precedent for treating PLC/control logic as a formal-verification target.

Relevant framing point from this literature: even small changes in control
logic directly alter physical process behaviour, which is exactly why an
equivalence oracle is non-negotiable for our transformations — and a good
sentence for the paper's motivation.

---

## 5. Where our novelty actually sits

Given Galápagos exists, "LLM generates equivalent program variants" is **not**
novel on its own and should not be claimed as such. What appears unclaimed:

1. **Length-targeted diversification.** I found no work where the user
   specifies a target program size and the generator produces an equivalent
   variant to that size. Galápagos, Auto-SPT and the sosie work all generate
   variants without size control. This is our most defensible novel element.
2. **Migration and diversification merged in one pipeline.** Nothing found
   that combines library modernisation with diversification — and the
   defense-in-depth argument (migration removes *known* CVEs, diversification
   hardens against *unknown* ones) follows naturally from that pairing.
3. **Merge rather than ensemble.** All the prior work produces N artifacts to
   run redundantly. A single merged artifact is a different deliverable.
4. **ICS control loops as the corpus**, with an oracle that includes
   invariants and branch-coverage proof rather than test-passing alone.

**Risk to be aware of:** Galápagos is recent (TOSEM 2025) and from the leading
group in this area. A reviewer who knows it will expect us to cite it and to
say precisely how we differ. The four points above are the answer, and they
are strongest when stated up front rather than defended later.
