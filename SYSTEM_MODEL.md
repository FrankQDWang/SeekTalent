# SeekTalent System Model

This document is the only active canonical model for SeekTalent. It is written as a compact algorithm note rather than an implementation guide. Symbols are logical, not code-shaped. The code is expected to follow this model; the model is not expected to mirror code field names.

Let the raw input be a job description and hiring notes,

$$
x=(J,N),
$$

and let the system output be a bounded search run

$$
\mathcal{R}(x)=\left(G^\star,\; s_{\text{run}},\; \omega_{\text{stop}}\right),
$$

where \(G^\star\) is the final shortlist, \(s_{\text{run}}\) is a run-level summary, and \(\omega_{\text{stop}}\) is the terminal stop reason.

## 1. Problem Formulation

SeekTalent is a bounded, phase-aware search process over a frontier of query nodes. It is not a one-shot rerank and not an unconstrained iterative retrieval loop.

The system first freezes a normalized requirement representation,

$$
R = \mathcal{N}(J,N),
$$

then freezes a scoring policy,

$$
P_{\text{score}}=\mathcal{F}(R),
$$

and finally runs a phased frontier search over CTS under a finite round budget \(B\).

The core design choice is that CTS keyword search is conjunctive: adding query terms usually tightens retrieval. Therefore the system cannot treat query growth as a free monotone improvement. Selection, rewrite, evidence mining, and stopping are all built around this constraint.

*Implementation anchor.* `bootstrap_llm.py`, `bootstrap_ops.py`, `runtime/orchestrator.py`  
*Trace anchor.* `SearchRunBootstrapArtifact`, `SearchRunBundle.final_result`

## 2. Derived Objects and Runtime State

The normalized requirement sheet is

$$
R = \left(r_{\text{title}},\; r_{\text{summary}},\; C_{\text{must}},\; C_{\text{pref}},\; C_{\text{hard}},\; C_{\text{neg}}\right).
$$

Here:

- \(r_{\text{title}}\) is the role title.
- \(r_{\text{summary}}\) is the role summary.
- \(C_{\text{must}}\) is the must-have capability set.
- \(C_{\text{pref}}\) is the preferred capability set.
- \(C_{\text{hard}}\) is the hard-constraint bundle.
- \(C_{\text{neg}}\) is the exclusion signal set.

The runtime frontier state before round \(t\) is

$$
F_t=\left(\mathcal{N}_t,\; \mathcal{O}_t,\; G_t,\; \Pi_t,\; b_t\right),
$$

where:

- \(\mathcal{N}_t\) is the set of frontier nodes.
- \(\mathcal{O}_t \subseteq \mathcal{N}_t\) is the set of open nodes.
- \(G_t\) is the run-level shortlist accumulated so far.
- \(\Pi_t\) stores operator statistics.
- \(b_t\) is the remaining round budget.

Each frontier node \(n\) carries at least:

$$
n=\left(q(n),\; k(n),\; z(n),\; L(n),\; \eta(n),\; \rho(n),\; \sigma(n)\right),
$$

where:

- \(q(n)\) is the node query term pool.
- \(k(n)\) is the knowledge-pack provenance.
- \(z(n)\) is the negative-term set.
- \(L(n)\) is the node-local shortlist.
- \(\eta(n)\) is the last branch evaluation, if any.
- \(\rho(n)\) is the reward breakdown, if any.
- \(\sigma(n)\in\{\texttt{open},\texttt{closed}\}\) is the node status.

The text-match primitive used across runtime is a token-aware phrase predicate

$$
H(Q,c)\in\{0,1\},
$$

implemented by a single shared function. It returns whether a capability or allowlist phrase \(c\) is matched by a sequence of query or text phrases \(Q\). This single-owner constraint is structural, not cosmetic.

*Implementation anchor.* `query_terms.py`, `models.py`, `runtime/orchestrator.py`  
*Trace anchor.* `frontier_state_before`, `frontier_state_after`, `controller_context.runtime_budget_state`

## 3. Bootstrap

Bootstrap freezes three objects before runtime search:

1. domain routing output,
2. the scoring policy,
3. the round-0 seed set.

Formally,

$$
R \xrightarrow{\text{routing}} K, \qquad
R \xrightarrow{\text{policy freeze}} P_{\text{score}}, \qquad
(R,K) \xrightarrow{\text{seed generation}} S_0.
$$

The scoring policy contains:

- fit-gate constraints,
- fusion weights,
- reranker calibration,
- rerank instruction,
- rerank query text.

The initial frontier is then

$$
F_0 = \mathcal{I}(S_0,R,K,P_{\text{score}}).
$$

Round-0 is not a special query-budget regime anymore. It uses the same query-term cap as the runtime `explore` phase:

$$
u_0 = u_{\text{explore}}.
$$

*Implementation anchor.* `route_domain_knowledge_pack`, `freeze_scoring_policy`, `generate_bootstrap_output`, `initialize_frontier_state`  
*Trace anchor.* `routing_result`, `scoring_policy`, `bootstrap_output`, `frontier_state`

## 4. Phase Progression

Let the initial runtime budget be \(B \in [5,12]\), and let the round index be \(t \ge 0\). Define the normalized phase progress

$$
\phi(t)=\frac{t}{\max(1,B-1)}.
$$

The runtime phase is

$$
\mathrm{phase}(t)=
\begin{cases}
\texttt{explore}, & \phi(t)<0.34, \\
\texttt{balance}, & 0.34 \le \phi(t)<0.67, \\
\texttt{harvest}, & \phi(t)\ge 0.67.
\end{cases}
$$

The phase is a shared control variable used by:

- frontier selection,
- operator legality,
- query-term budget,
- stop gating,
- run diagnostics.

This is a hard shared owner. Phase-specific policies must be functions of the same runtime budget state, not separately reconstructed.

*Implementation anchor.* `runtime_budget.build_runtime_budget_state`  
*Trace anchor.* `runtime_budget_state`, `search_phase_by_search_round`

## 5. Active Node Selection

At each round, the controller selects one open node \(n \in \mathcal{O}_t\) as the active node. The selection score is a six-term weighted sum.

### 5.1 Operator Exploitation

Let \(\bar r(n)\) be the historical average reward of the operator attached to node \(n\). Then

$$
S_{\text{exploit}}(n)=\frac{\max(\bar r(n),0)}{1+\max(\bar r(n),0)}.
$$

This maps nonnegative operator reward into \([0,1)\).

### 5.2 Operator Exploration

Let \(N\) be the total number of operator selections so far, and let \(N_n\) be the number of times the operator of node \(n\) has been selected. Then

$$
S_{\text{explore}}(n)=\sqrt{\frac{2\ln(N+2)}{N_n+1}}.
$$

This is a UCB-style exploration bonus.

### 5.3 Coverage Opportunity

Let \(K=|C_{\text{must}}|\), and let

$$
h(n)=\sum_{c\in C_{\text{must}}} H(q(n),c).
$$

Define

$$
\gamma(n)=\frac{h(n)}{\max(1,K)},
$$

and the coverage score

$$
S_{\text{cover}}(n)=
\begin{cases}
\gamma(n), & 0<\gamma(n)<1, \\
0, & \text{otherwise}.
\end{cases}
$$

Only partial coverage is rewarded. Zero coverage and full coverage both receive zero opportunity score.

### 5.4 Incremental Value

If node \(n\) has no previous reward breakdown, set

$$
S_{\text{incr}}(n)=0.
$$

Otherwise let \(y(n)\) be the node's new-fit yield count and \(d(n)\) its diversity score. The bounded incremental value is

$$
S_{\text{incr}}(n)=0.7\cdot \frac{y(n)}{1+y(n)} + 0.3\cdot d(n).
$$

### 5.5 Freshness Bonus

$$
S_{\text{fresh}}(n)=
\begin{cases}
1, & \eta(n)=\varnothing, \\
0, & \text{otherwise}.
\end{cases}
$$

### 5.6 Redundancy Penalty

Let \(L(n)\) be the node shortlist and \(G_t\) the run shortlist before round \(t\). Then

$$
S_{\text{redund}}(n)=
\begin{cases}
0, & L(n)=\varnothing, \\
\dfrac{|L(n)\cap G_t|}{|L(n)|}, & \text{otherwise}.
\end{cases}
$$

### 5.7 Phase-Weighted Selection

The final selection score is

$$
\mathrm{Score}(n)
=
w_1 S_{\text{exploit}}(n)
+w_2 S_{\text{explore}}(n)
+w_3 S_{\text{cover}}(n)
+w_4 S_{\text{incr}}(n)
+w_5 S_{\text{fresh}}(n)
-w_6 S_{\text{redund}}(n).
$$

The active weights are phase-dependent:

| phase | \(w_1\) exploit | \(w_2\) explore | \(w_3\) coverage | \(w_4\) incr | \(w_5\) fresh | \(w_6\) redund |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| explore | 0.6 | 1.6 | 1.2 | 0.2 | 0.8 | 0.4 |
| balance | 1.0 | 1.0 | 0.8 | 0.8 | 0.3 | 0.8 |
| harvest | 1.4 | 0.3 | 0.2 | 1.2 | 0.0 | 1.2 |

Thus `explore` favors coverage and operator uncertainty, while `harvest` favors operator exploitation, realized yield, and de-duplication.

*Implementation anchor.* `frontier_ops.select_active_frontier_node`, `_selection_breakdown`  
*Trace anchor.* `selection_ranking`, `active_selection_breakdown`

## 6. Operator Legality Surface

After the active node is fixed, the controller does not choose from the full operator catalog. It chooses from a phase-gated legality surface.

Let \(U(n)\) be the unmet must-have set of the active node, let \(D(n)\) indicate whether legal crossover donors exist, and let \(K(n)\) indicate whether the node carries knowledge-pack provenance.

The available operator set is

$$
\mathcal{A}(n,t)=
\begin{cases}
\{\texttt{must\_have\_alias},\texttt{generic\_expansion},\texttt{core\_precision},\texttt{relaxed\_floor}\} \cup \mathcal{P}(n), & \texttt{explore},\\[4pt]
\{\texttt{core\_precision},\texttt{must\_have\_alias},\texttt{relaxed\_floor},\texttt{generic\_expansion}\} \cup \mathcal{P}(n)\cup \mathcal{C}(n), & \texttt{balance},\\[4pt]
\{\texttt{core\_precision}\}\cup \mathcal{C}(n)\cup \mathcal{E}(n), & \texttt{harvest},
\end{cases}
$$

where

$$
\mathcal{P}(n)=
\begin{cases}
\{\texttt{pack\_expansion},\texttt{cross\_pack\_bridge}\}, & K(n)=1,\\
\varnothing, & K(n)=0,
\end{cases}
$$

$$
\mathcal{C}(n)=
\begin{cases}
\{\texttt{crossover\_compose}\}, & D(n)=1,\\
\varnothing, & D(n)=0,
\end{cases}
$$

and

$$
\mathcal{E}(n)=
\begin{cases}
\{\texttt{must\_have\_alias},\texttt{generic\_expansion}\}, & U(n)\neq\varnothing,\\
\varnothing, & U(n)=\varnothing.
\end{cases}
$$

So:

- `explore` never allows crossover,
- `balance` allows the full mature surface,
- `harvest` removes pack-driven exploration and keeps expansion only when unmet must-haves remain.

*Implementation anchor.* `_allowed_operator_names`, `_donor_candidate_summaries`  
*Trace anchor.* `allowed_operator_names`, `operator_surface_unmet_must_haves`, `donor_candidate_summaries`

## 7. Query Budget and Rewrite Legality

### 7.1 Query-Term Budget

The maximum query length is phase-dependent:

$$
u(\texttt{explore})=3,\qquad
u(\texttt{balance})=4,\qquad
u(\texttt{harvest})=6.
$$

Round-0 inherits the same cap as `explore`:

$$
u_0=3.
$$

This is the current implemented policy, even though it is not the only conceivable annealing schedule.

### 7.2 Non-Crossover Legality

Let \(q_a\) be the active-node query pool, and let \(q'\) be a proposed rewritten query.

For `core_precision`, legality requires

$$
q' \subseteq q_a,\qquad q'\neq \varnothing,
$$

with no new terms.

For `relaxed_floor`, legality requires

$$
q' \subset q_a,\qquad q'\neq \varnothing,
$$

that is, a non-empty strict subset.

For the rewrite operators

$$
\{\texttt{must\_have\_alias},\texttt{generic\_expansion},\texttt{pack\_expansion},\texttt{cross\_pack\_bridge}\},
$$

legality requires all three conditions:

$$
q' \cap q_a \neq \varnothing,\qquad
q' \setminus q_a \neq \varnothing,\qquad
q_a \setminus q' \neq \varnothing.
$$

Hence a rewrite must preserve some active anchor, introduce some new term, and drop some old term. Non-crossover search is a true rewrite, not append-only expansion.

### 7.3 Crossover Legality

For `crossover_compose`, the query is built from shared anchors and donor terms. Legal donors must satisfy:

- positive reward above threshold,
- at least one shared anchor with the active node,
- support for at least one unmet must-have of the active node.

*Implementation anchor.* `runtime_budget.py`, `frontier_ops._validate_non_crossover_query_terms`, `search_ops.materialize_search_execution_plan`  
*Trace anchor.* `max_query_terms`, `controller_decision`, `execution_plan`

## 8. Evidence Mining and GA-lite Rewrite Ranking

### 8.1 Evidence Term Pool

From a completed CTS round, the system constructs a rewrite-only evidence pool from up to five top fit candidates. Evidence terms are mined from strong fields:

- title,
- project names,
- work summaries,
- work-experience summaries,
- search text.

Terms already present in the current query are rejected. Generic junk and topic drift are hard rejects.

Let \(\tau\) be a candidate rewrite term. Its accepted evidence score is

$$
S_{\text{ev}}(\tau)
=
S_{\text{sup}}(\tau)
+S_{\text{qual}}(\tau)
+S_{\text{field}}(\tau)
+B_{\text{must}}(\tau)
+B_{\text{anchor}}(\tau)
+B_{\text{pack}}(\tau)
-P_{\text{generic}}(\tau),
$$

where:

$$
S_{\text{sup}}(\tau)=\min\left(3,\; |\mathcal{C}_\tau|\right),
$$

$$
S_{\text{qual}}(\tau)=\text{mean fusion score of supporting candidates},
$$

$$
S_{\text{field}}(\tau)=\max_{f\in \mathcal{F}_\tau} w_f,
$$

with field weights

$$
w_{\text{title}}=1.0,\;
w_{\text{project}}=0.9,\;
w_{\text{work}}=0.8,\;
w_{\text{experience}}=0.7,\;
w_{\text{search}}=0.4.
$$

The bonuses are:

$$
B_{\text{must}}\in\{0,1.5\},\qquad
B_{\text{anchor}}\in\{0,0.75\},\qquad
B_{\text{pack}}\in\{0,0.5\},
$$

and the generic penalty is

$$
P_{\text{generic}}(\tau)=\min(0.75,\; 0.25 \cdot g(\tau)),
$$

where \(g(\tau)\) counts generic fragments.

Single-source evidence is allowed only if it is clearly high-signal: either it repairs an unmet must-have, matches pack provenance, or is backed by a strong `title/project_names` source with mean fusion score at least \(0.85\).

The final accepted pool is the top six evidence terms sorted by

1. accepted evidence score,
2. support count,
3. lexical order.

### 8.2 GA-lite Rewrite Search

Let \(q^{(0)}\) be the controller draft query. GA-lite does not invent an arbitrary search over the full term space. It constructs a bounded local candidate population by replacing a small number of non-anchor terms in \(q^{(0)}\) with terms from the evidence pool. The legal population is capped at six candidates.

Define the seed-anchor set as

$$
A^{(0)}=
\left(q^{(0)} \cap q_a\right)
\;\text{or, if empty, the first term of } q^{(0)}.
$$

Each legal candidate \(q\) is scored by

$$
F(q)=
\alpha_1 M(q)
+\alpha_2 A(q)
+\alpha_3 C(q)
+\alpha_4 P(q)
-\alpha_5 L(q)
-\alpha_6 R(q),
$$

with default weights

$$
(\alpha_1,\alpha_2,\alpha_3,\alpha_4,\alpha_5,\alpha_6)
=(1.4,\;1.0,\;1.2,\;0.8,\;0.35,\;0.45).
$$

The sub-scores are:

- must-have repair
  $$
  M(q)=\frac{1}{|U(n)|}\sum_{c\in U(n)} H(q,c),
  $$
  with \(M(q)=0\) if \(U(n)=\varnothing\);
- anchor preservation
  $$
  A(q)=\frac{|A^{(0)}\cap q|}{\max(1,|A^{(0)}|)};
  $$
- rewrite coherence \(C(q)\), defined from evidence strength, alignment, and multi-term source agreement;
- provenance coherence \(P(q)\), defined from field strength, support strength, and source overlap;
- query length penalty
  $$
  L(q)=\frac{|q|}{u(\mathrm{phase}(t))};
  $$
- redundancy penalty
  $$
  R(q)=\frac{|q\cap q_a|}{\max(1,|q|)}.
  $$

More explicitly,

$$
C(q)=0.45\,C_{\text{strength}}(q)+0.35\,C_{\text{align}}(q)+0.20\,C_{\text{agree}}(q),
$$

and

$$
P(q)=0.40\,P_{\text{field}}(q)+0.35\,P_{\text{sup}}(q)+0.25\,P_{\text{overlap}}(q).
$$

Thus the rewrite ranker prefers legal rewrites that preserve the draft anchor, repair missing requirements, and remain semantically supported by coherent evidence provenance.

*Implementation anchor.* `rewrite_evidence.py`, `frontier_ops._ga_lite_query_rewrite`, `frontier_ops._rewrite_fitness`  
*Trace anchor.* `rewrite_term_pool`, `rewrite_choice_trace`

## 9. CTS Execution and Candidate Scoring

Each legal controller decision materializes a CTS execution plan

$$
\pi_t = \left(q_t,\; C_{\text{hard}},\; C_{\text{runtime}},\; m_t\right),
$$

where:

- \(q_t\) is the final query term list,
- \(C_{\text{hard}}\) are projected hard constraints,
- \(C_{\text{runtime}}\) are runtime-only constraints such as must-have and negative keywords,
- \(m_t\) is the target new-candidate count.

For reranking, the system uses a text-only contract:

- the query is a short natural-language role summary,
- each resume is passed as text,
- no JSON-structured rerank prompt is used.

The rerank query currently concatenates:

1. role title,
2. role summary,
3. must-have capabilities,
4. location,
5. min/max years,
6. degree requirement,
7. target company background,
8. target school background,
9. preferred capabilities.

Age and gender remain fit-gate signals and are not injected into the rerank query.

Let \(s_r^{\text{raw}}\) be the reranker output for a candidate. After clipping and affine adjustment, the normalized rerank score is

$$
s_r = \sigma\!\left(\frac{\mathrm{clip}(s_r^{\text{raw}}+\delta,\; a,\; b)}{\tau}\right),
$$

where \((a,b,\delta,\tau)\) come from the frozen reranker calibration.

Let \(m(c)\) and \(p(c)\) be the must-have and preferred match fractions, computed with the same shared token-aware hit predicate \(H\) used by runtime selection and rewrite evidence. Let \(r(c)\) be the candidate risk score. The fusion score is

$$
s_f(c)=
\lambda_1 s_r(c)
+\lambda_2 m(c)
+\lambda_3 p(c)
-\lambda_4 r(c),
$$

with default weights

$$
(\lambda_1,\lambda_2,\lambda_3,\lambda_4)
=(0.55,\;0.25,\;0.10,\;0.10).
$$

The final fit gate is binary:

$$
\mathrm{fit}(c)\in\{0,1\},
$$

and requires all active hard checks to pass:

- location allowlist,
- min/max years,
- min/max age,
- gender requirement,
- company allowlist,
- school allowlist,
- degree requirement.

Missing evidence does not automatically fail the fit gate. The system stays permissive under absent structured fields.

*Implementation anchor.* `rerank_text.py`, `search_ops.py`, `bootstrap_ops.freeze_scoring_policy`  
*Trace anchor.* `scoring_policy`, `execution_result`, `scoring_result`

## 10. Reward and Frontier Update

After CTS execution and candidate scoring, the system evaluates the branch and computes a node reward. Let:

- \(\Delta_{\text{top3}}\) be the change in average top-three fusion score against the parent node baseline,
- \(g_{\text{must}}\) be the mean must-have match score over parent-net-new shortlist rows,
- \(y\) be the number of run-net-new shortlist candidates,
- \(\nu\) be branch novelty,
- \(u\) be branch usefulness,
- \(d\) be diversity,
- \(p_{\text{stab}}\) be the mean risk score over shortlist rows,
- \(v_{\text{hard}}\) be the fraction of scored rows failing the fit gate,
- \(p_{\text{dup}}\) be the CTS duplicate rate,
- \(p_{\text{cost}}\) be the page-fetch cost penalty.

The cost term is

$$
p_{\text{cost}}=\min(1,\;0.15\cdot \mathrm{pages\_fetched}),
$$

and the reward is

$$
r =
2.0\Delta_{\text{top3}}
+ 1.5g_{\text{must}}
+ 0.6y
+ 0.5\nu
+ 0.5u
+ 0.4d
- 0.8p_{\text{stab}}
- 1.0v_{\text{hard}}
- 0.6p_{\text{dup}}
- 0.4p_{\text{cost}}.
$$

Operator reward is updated by incremental mean:

$$
\bar r \leftarrow \frac{\bar r \cdot N_{\text{old}} + r}{N_{\text{old}} + 1}.
$$

The frontier then adds a child node whose query pool is the final query actually executed in the round. For non-crossover search, the child inherits the rewritten query directly rather than appending terms onto the parent query.

*Implementation anchor.* `compute_node_reward_breakdown`, `update_frontier_state`  
*Trace anchor.* `branch_evaluation`, `reward_breakdown`, `frontier_state_after`

## 11. Stop Policy

The run stop condition is phase-gated.

Two stop reasons dominate unconditionally:

$$
\omega_{\text{stop}}=
\begin{cases}
\texttt{budget\_exhausted}, & b_t \le 0,\\
\texttt{no\_open\_node}, & \mathcal{O}_t=\varnothing,\\
\cdots & \text{otherwise}.
\end{cases}
$$

The effective stop guard at round \(t\) is

$$
G_t^{\text{stop}}=
\left(
\mathbf{1}_{\mathrm{phase}(t)\in\{\texttt{balance},\texttt{harvest}\}},
\mathbf{1}_{\mathrm{phase}(t)=\texttt{harvest}},
\theta_\nu,\theta_u,\theta_r
\right),
$$

with default floors

$$
(\theta_\nu,\theta_u,\theta_r)=(0.25,\;0.25,\;1.5).
$$

Thus:

- `controller_stop` is legal only in `balance` and `harvest`;
- `exhausted_low_gain` is legal only in `harvest`.

The `exhausted_low_gain` condition is

$$
\texttt{branch\_exhausted}
\;\land\;
\nu < \theta_\nu
\;\land\;
u < \theta_u
\;\land\;
r < \theta_r.
$$

Hence the runtime never stops globally in `explore` merely because of a locally weak branch.

*Implementation anchor.* `build_effective_stop_guard`, `evaluate_stop_condition`  
*Trace anchor.* `effective_stop_guard`, `stop_reason`

## 12. Trace, Diagnostics, and Structural Invariants

The run bundle is not only a log. It is the observability surface for debugging and offline tuning.

Per-round trace stores at least:

- controller prompt audit,
- branch-evaluation prompt audit,
- final controller decision,
- execution plan,
- execution result,
- scoring result,
- rewrite term pool,
- rewrite choice trace,
- reward breakdown,
- frontier state before/after,
- effective stop guard.

Run-level diagnostics store at least:

- search-phase sequence,
- operator sequence,
- must-have query coverage by search round,
- net-new shortlist gain by search round,
- per-phase operator distributions.

The hard invariants of the current system are:

1. The same text-match predicate \(H\) must be used by:
   - coverage opportunity,
   - unmet must-have detection,
   - evidence topic-drift gating,
   - scoring-layer text matching.
2. Round-0 query budget must equal the `explore` query budget.
3. Controller draft validation and runtime normalization must use the same rewrite fitness weights.
4. Non-crossover child nodes inherit executed query terms directly.
5. Evidence terms are rewrite-only inputs; they are not directly appended into CTS queries.

Violation of any of these invariants causes the runtime to become self-inconsistent even if individual operators still appear locally correct.

*Implementation anchor.* `prompt_surfaces.py`, `run_artifacts.py`, `controller_llm.py`, `runtime/orchestrator.py`  
*Trace anchor.* `SearchRoundArtifact`, `SearchRunBundle.eval`, `eval.json`

## 13. Future Experiments

The current model is intentionally bounded. The main deferred directions are:

- continuous phase annealing instead of the current three-segment schedule,
- optional one-shot CTS probe for top rewrite candidates,
- stronger replay-driven tuning of selection and rewrite weights,
- multi-query fan-out under conjunctive CTS semantics,
- capability ontology beyond the current shared lexical hit predicate,
- a separate final candidate presentation layer.

These are future model changes, not hidden assumptions of the current one.
