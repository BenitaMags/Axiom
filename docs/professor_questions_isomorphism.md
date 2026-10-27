# Email draft

**Subject:** Tomorrow's meeting, and a proposal on measuring diversity

---

Dear Professor [Name],

I'm afraid I won't be able to join the meeting tomorrow. I need to go into
college first thing in the morning to sort out some tuition issues, and to be
honest I haven't made enough progress on the agent yet for the meeting to be a
good use of your time. Could we push it to later in the week? I should have
something concrete by then.

In the meantime, one thing I'd like your view on.

**Measuring diversity.** Reading around the area, it seems that generating
equivalent variants is only half of what these papers report — they also
measure how *different* the variants actually are, and I think we should plan
to do the same. Baudry, Allier and Monperrus (ISSTA 2014) synthesise variants
that keep the original functionality but execute differently, and then measure
the resulting computation diversity. Galápagos (Ron et al., TOSEM 2025) does
the same with LLM-generated variants, reporting both static difference and
diverging runtime behaviour. Auto-SPT (Hooda et al., 2025) goes further and
shows formally that the diversity of the individual transformations determines
how strong their composition is — which suggests our agent should cycle
through several transformation types rather than applying one repeatedly,
especially when it's trying to reach a line-count target.

Concretely, I'd propose measuring four things per variant:

- a structural measure such as Program Dependence Graph distance or Define-Use
  chain similarity — these ignore pure renaming, so cosmetic changes can't
  inflate the numbers;
- dynamic trace distance, comparing the executed instruction path at runtime;
- Normalized Compression Distance, as a cheap whole-file summary;
- memory-footprint divergence, which ties directly to the memory point we
  discussed.

The reason I think this matters for us specifically: because we're producing a
single merged file rather than several variants side by side, we can't show
diversity by comparison — it has to be measured, or the diversification half
of the contribution is asserted rather than demonstrated. Would you prefer one
headline metric with the others as supporting evidence, or all four reported
together?

**One missing file.** `harness.py` refers to a README that I don't think was
included — could you send it when you get a chance? I expect it has the
transformation catalogue and equivalence rules you have in mind.

Best regards,
Benita

---

**References**

1. J. Ron, D. Gaspar, J. Cabrera-Arteaga, B. Baudry, M. Monperrus,
   "Galápagos: Automated N-Version Programming with LLMs," *ACM TOSEM*, 2025.
   doi:10.1145/3785363 — https://arxiv.org/abs/2408.09536
2. A. Hooda, M. Christodorescu, C. Ren, A. Wilson, K. Fawaz, S. Jha,
   "Auto-SPT: Automating Semantic Preserving Transformations for Code,"
   arXiv:2512.06042, 2025. — https://arxiv.org/abs/2512.06042
3. B. Baudry, S. Allier, M. Monperrus, "Tailored Source Code Transformations
   to Synthesize Computationally Diverse Program Variants," *Proc. ISSTA
   2014*. doi:10.1145/2610384.2610415 — https://arxiv.org/abs/1401.7635
