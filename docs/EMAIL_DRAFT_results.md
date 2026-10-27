Subject: AXIOM evaluation — four-arm results, and four measurement defects I had to fix first

Hi Professor [NAME],

The four-arm evaluation is complete: 12 programs x 3 seeds x 4 configurations,
144 runs, all on claude-sonnet-5 at temperature 0.7. The full results are
attached as results_FOUR_ARM_FINAL.csv, with a limitations write-up in
LIMITATIONS.md.

Before the numbers, the most important thing I found: the earlier version of
this result was distorted by defects in my own evaluation harness, not by the
systems being measured. I list them below because they change how the earlier
figures should be read.

FOUR MEASUREMENT DEFECTS, NOW CORRECTED

  1. The harness pre-installed only the corpus's declared target libraries,
     but every arm chooses its own. Migrations to libraries we had not
     installed were scored as model failures. In one arm, 6 of 8 recorded
     failures were this artifact.

  2. A PyPI distribution name was being used where the import root was needed
     ("paho-mqtt" looked up as "paho_mqtt"; the root is "paho"). The same
     wrong key appeared in four independent places, so each silently matched
     nothing and the migration never completed.

  3. The oracle's pass/fail flag was dropped when per-module reports were
     merged, so 16 of 18 C/D rows recorded oracle_equiv=N beside a detail
     field reading "all four layers passed".

  4. "No-op" was defined as zero lines changed, which missed clones that
     changed only comments while never removing the dependency.

Correcting these moved proteus_full from 12/36 to 23/36 on migration_success
with no change to any prompt. All four arms were then re-measured identically.
Numbers from before this correction should not be used.

RESULTS

| Arm | migration_success | Clones judged | CLEAN | CLEAN % | Cost |
|---|---|---|---|---|---|
| single_shot | 32/36 | 32 | 2 | 6% | $0.79 |
| single_shot_repair | **34/36** | 34 | 7 | 21% | $0.96 |
| proteus_no_verif | 24/36 | 27 | 4 | 15% | $4.79 |
| proteus_full | 23/36 | 25 | 6 | **24%** | $5.94 |

"migration_success" is the harness's verdict: the dependency was replaced, the
clone compiles, and the tests pass (plus the equivalence oracle on C/D).
"CLEAN" is a blind LLM reviewer's verdict that the rewrite is behaviourally
faithful, counted only over clones that actually migrated.

WHAT I TAKE FROM THIS

  1. Passing the test suite is a weak proxy for migration correctness. Across
     every arm, 55-59% of the migrations the harness accepted contained
     defects the reviewer rated MAJOR or BROKEN — all of them compiling and
     passing 100% of their tests. The best arm is 24% behaviourally faithful.

     The cause is structural rather than incidental. Every corpus program
     dependency-injects its external client so the suite can run without a
     live server, and migration rewrites exactly that code path — so the tests
     never execute the code the migration produced. A worked example:
     proteus_full's Modbus adapter accepts a "unit" (slave id) argument and
     silently discards it, so every read targets the wrong device on a
     multi-drop bus. It passed compilation, the test suite, and the patch
     integrity check. The reviewer caught it in one pass.

  2. A single prompt plus one verifier-fed repair call is the strongest
     configuration tested — 34/36 migrations, zero no-ops, zero BROKEN
     clones, at $0.96 against $5.94 for the full agent pipeline.

  3. The verification ablation shows up in quality, not in quantity.
     proteus_full and proteus_no_verif are indistinguishable on migration
     count (23 vs 24) but differ on review outcome (24% vs 15% CLEAN). That
     effect is suggestive rather than significant at n=25 and n=27.

  4. Two programs (C1, C2) require removing a dependency and reimplementing
     its behaviour as first-party code. The agent pipeline has no path to
     this and scores 0/6 on them, which is roughly half its remaining gap to
     the baselines. This is a capability boundary, not a tuning failure.

LIMITATIONS WORTH FLAGGING

  - The equivalence oracle has recorded probes only for categories C and D.
    Half the corpus is gated on compilation and tests alone.
  - 3 seeds per cell. Differences of 1-2 rows out of 36 are within run-to-run
    variance; only the large effects are safely outside noise.
  - The reviewer is a single model, one pass, with no inter-rater agreement
    measured. 4 of 144 rows returned unparseable or empty replies and are
    excluded. Those exclusions are not random: one contained a legible BROKEN
    finding before it was truncated, and dropping it flatters
    single_shot_repair, the only arm otherwise showing zero BROKEN clones.
  - 12 synthetic industrial-control programs, one language, one model. Nothing
    here establishes behaviour on real-world codebases.

The honest summary is that the agent pipeline improved substantially once the
harness stopped mismeasuring it, and still does not beat a much simpler and
much cheaper baseline on this corpus — and that no configuration produces code
I would consider ready to deploy without human review.

Happy to walk through any of this, and to discuss whether the test-passage
finding is worth developing as the paper's main contribution rather than the
arm comparison.

Best,
Benita
