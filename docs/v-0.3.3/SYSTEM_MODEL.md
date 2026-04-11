# SeekTalent System Model

This document is the active canonical model for `SeekTalent v0.3.3`. It is written as a compact algorithm note rather than an implementation guide. Symbols are logical, not code-shaped.

Let the raw input be a job description and hiring notes,

$$
x=(J,N),
$$

and let the target system output be a bounded search run

$$
\mathcal{R}(x)=\left(\Gamma^\star,\; e_{\text{run}},\; s_{\text{run}},\; \omega_{\text{stop}}\right),
$$

where:

- \(\Gamma^\star\) is the ordered reviewer-ready candidate-card set,
- \(e_{\text{run}}\) is the run diagnostics bundle,
- \(s_{\text{run}}\) is the textual run summary surface,
- \(\omega_{\text{stop}}\) is the terminal stop reason.

## 1. Problem Formulation

SeekTalent is a bounded recruiter search runtime over conjunctive CTS keyword retrieval. It is not a one-shot rerank, not a JD-to-resume scoring appliance, and not an unconstrained agent loop.

The system first freezes a normalized requirement representation,

$$
R = \mathcal{N}(J,N),
$$

then freezes a scoring policy,

$$
P_{\text{score}}=\mathcal{F}(R),
$$

and finally runs a phased frontier search over CTS under a finite round budget \(B\).

The core design choice remains unchanged: CTS keyword search is conjunctive, so adding terms usually tightens retrieval. Query growth is therefore not a free monotone improvement. Bootstrap, repair, evidence mining, scoring, and stopping must all respect that constraint.

The target model keeps four high-order commitments:

1. root anchor branches remain persistent truth branches,
2. repair branches are hypotheses rather than replacements,
3. final output must be reviewer-ready rather than rank-only,
4. run evaluation must include session and business outcomes rather than internal search diagnostics alone.

*Target owner anchor.* `requirements/normalization.py`, `bootstrap_ops.py`, `runtime/orchestrator.py`  
*Target trace anchor.* `SearchRunBootstrapArtifact`, `SearchRunBundle.final_result`, `SearchRunBundle.eval`

## 2. Derived Objects, Branch Roles, and Runtime State

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
- \(\mathcal{O}_t \subseteq \mathcal{N}_t\) is the currently selectable open-node set.
- \(G_t\) is the run-level shortlist accumulated so far.
- \(\Pi_t\) stores operator statistics.
- \(b_t\) is the remaining round budget.

Each frontier node \(n\) carries at least

$$
n=\left(q(n),\; k(n),\; z(n),\; L(n),\; \beta(n),\; a(n),\; \eta(n),\; \rho(n),\; \sigma(n)\right),
$$

where:

- \(q(n)\) is the node query term pool.
- \(k(n)\) is the knowledge-pack provenance.
- \(z(n)\) is the negative-term set.
- \(L(n)\) is the node-local shortlist.
- \(\beta(n)\in\{\texttt{root\_anchor},\texttt{repair\_hypothesis}\}\) is the branch role.
- \(a(n)\) is `FrontierNode_t.root_anchor_frontier_node_id`.
- \(\eta(n)\) is the last branch evaluation, if any.
- \(\rho(n)\) is the reward breakdown, if any.
- \(\sigma(n)\in\{\texttt{open},\texttt{closed}\}\) is the node status.

The branch-role semantics are hard structural facts:

$$
\beta(n)=\texttt{root\_anchor} \Longrightarrow a(n)=\mathrm{id}(n),
$$

and

$$
\beta(n)=\texttt{repair\_hypothesis} \Longrightarrow a(n)\in \mathcal{A}_0,
$$

where \(\mathcal{A}_0\) is the set of root-anchor node ids created during bootstrap.

The text-match primitive used across runtime remains a single shared token-aware phrase predicate

$$
H(Q,c)\in\{0,1\},
$$

implemented by one owner. It returns whether a capability or allowlist phrase \(c\) is matched by a sequence of query or text phrases \(Q\).

Two target contracts follow from this section:

- `FrontierNode_t.branch_role`
- `FrontierNode_t.root_anchor_frontier_node_id`

These contracts are public runtime state, not controller-only metadata.

*Target owner anchor.* `models.py`, `query_terms.py`, `runtime/orchestrator.py`  
*Target trace anchor.* `frontier_state_before`, `frontier_state_after`, `controller_context.runtime_budget_state`

## 3. Bootstrap and Root Anchor Initialization

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

The scoring policy still contains:

- fit-gate constraints,
- fusion weights,
- reranker calibration,
- rerank instruction,
- rerank query text.

The target bootstrap changes only one structural assumption: round-0 must generate at least one seed whose branch role is `root_anchor`.

Thus the initial frontier is

$$
F_0 = \mathcal{I}(S_0,R,K,P_{\text{score}}),
$$

with the additional constraint

$$
\exists n\in \mathcal{N}_0 \; \text{s.t.} \; \beta(n)=\texttt{root\_anchor}.
$$

If bootstrap emits multiple anchor-like seeds, each seed must still declare its own anchor lineage explicitly through `root_anchor_frontier_node_id`. No later repair branch may erase that lineage.

Round-0 is still not a special query-budget regime. It inherits the `explore` term cap:

$$
u_0 = u_{\text{explore}}.
$$

*Target owner anchor.* `bootstrap_llm.py`, `bootstrap_ops.generate_bootstrap_output`, `bootstrap_ops.initialize_frontier_state`  
*Target trace anchor.* `routing_result`, `scoring_policy`, `bootstrap_output`, `frontier_state`

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

The phase remains a shared control variable used by:

- frontier selection,
- operator legality,
- query-term budget,
- stop gating,
- run diagnostics.

This is still a hard shared owner. Phase-specific policies must be functions of the same runtime budget state, not separately reconstructed.

*Target owner anchor.* `runtime_budget.build_runtime_budget_state`  
*Target trace anchor.* `runtime_budget_state`, `search_phase_by_search_round`

## 5. Active Node Selection

At each round, the controller selects one open node \(n \in \mathcal{O}_t\) as the active node. The selection score remains a six-term weighted sum:

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

The score terms keep their current semantic roles:

- operator exploitation,
- operator exploration,
- coverage opportunity,
- incremental value,
- freshness bonus,
- redundancy penalty.

The target branch-role change does not replace the selection score; it changes node eligibility and lifecycle.

### 5.1 Persistent Anchor Eligibility

Let

$$
\mathcal{O}^{\text{anchor}}_t=\{n \in \mathcal{O}_t : \beta(n)=\texttt{root\_anchor}\},
$$

and

$$
\mathcal{O}^{\text{repair}}_t=\{n \in \mathcal{O}_t : \beta(n)=\texttt{repair\_hypothesis}\}.
$$

The target runtime must maintain

$$
\mathcal{O}^{\text{anchor}}_t \neq \varnothing
$$

for every nonterminal round \(t\).

This does not force anchor execution every round. It means only that root anchors remain eligible truth branches and cannot be removed from the selectable frontier merely because a repair child was created.

### 5.2 Phase Weights

The phase-specific weighting scheme still favors:

- `explore`: coverage and operator uncertainty,
- `balance`: mixed exploitation and realized yield,
- `harvest`: exploitation, realized yield, and de-duplication.

The exact numeric weight table remains policy-owned. This target model does not require new hard-coded coefficients to express the branch-role change.

*Target owner anchor.* `frontier_ops.select_active_frontier_node`, `runtime_ops.update_frontier_state`  
*Target trace anchor.* `selection_ranking`, `active_selection_breakdown`, `open_frontier_node_ids`

## 6. Controlled Repair Surface

After the active node is fixed, the controller still chooses from a phase-gated candidate action surface. The target change is semantic: rewrite is now a controlled repair surface rather than the primary gain engine.

Let \(U(n)\) be the unmet must-have set of the active node, let \(D(n)\) indicate whether legal crossover donors exist, and let \(K(n)\) indicate whether the node carries pack provenance.

The semantic operator surface is

$$
\mathcal{A}_{\text{sem}}(n,t)=
\begin{cases}
\{\texttt{must\_have\_alias},\texttt{vocabulary\_bridge},\texttt{core\_precision},\texttt{relaxed\_floor}\}\cup\mathcal{P}(n), & \texttt{explore},\\[4pt]
\{\texttt{core\_precision},\texttt{must\_have\_alias},\texttt{relaxed\_floor},\texttt{vocabulary\_bridge}\}\cup\mathcal{P}(n)\cup\mathcal{C}(n), & \texttt{balance},\\[4pt]
\{\texttt{core\_precision}\}\cup\mathcal{C}(n)\cup\mathcal{E}(n), & \texttt{harvest},
\end{cases}
$$

where

$$
\mathcal{P}(n)=
\begin{cases}
\{\texttt{pack\_bridge}\}, & K(n)=1,\\
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
\{\texttt{must\_have\_alias},\texttt{vocabulary\_bridge}\}, & U(n)\neq\varnothing,\\
\varnothing, & U(n)=\varnothing.
\end{cases}
$$

The semantic meanings are:

- `core_precision`: keep only high-confidence anchor terms,
- `relaxed_floor`: drop over-tight terms while preserving anchor intent,
- `must_have_alias`: repair unmet must-have coverage via explicit aliasing,
- `vocabulary_bridge`: bridge lexical mismatch without turning into generic drift,
- `pack_bridge`: bridge pack-specific vocabulary with one-pack or two-pack provenance,
- `crossover_compose`: compose a bounded donor hypothesis only when it repairs missing requirements under shared anchor intent.

*Target owner anchor.* `frontier_ops.generate_search_controller_decision_with_trace`, `rewrite_evidence.build_rewrite_term_pool`  
*Target trace anchor.* `allowed_operator_names`, `operator_surface_unmet_must_haves`, `controller_decision`

## 7. Query Budget and Repair Legality

### 7.1 Query-Term Budget

The maximum query length remains phase-dependent:

$$
u(\texttt{explore})=3,\qquad
u(\texttt{balance})=4,\qquad
u(\texttt{harvest})=6.
$$

Round-0 still inherits the same cap as `explore`:

$$
u_0=3.
$$

### 7.2 Non-Crossover Legality

Let \(q_a\) be the active-node query pool, and let \(q'\) be a proposed rewritten query.

For `core_precision`, legality still requires

$$
q' \subseteq q_a,\qquad q'\neq \varnothing,
$$

with no new terms.

For `relaxed_floor`, legality still requires

$$
q' \subset q_a,\qquad q'\neq \varnothing.
$$

For the repair operators

$$
\{\texttt{must\_have\_alias},\texttt{vocabulary\_bridge},\texttt{pack\_bridge}\},
$$

legality requires all three conditions:

$$
q' \cap q_a \neq \varnothing,\qquad
q' \setminus q_a \neq \varnothing,\qquad
q_a \setminus q' \neq \varnothing.
$$

Thus a non-crossover repair must:

- preserve some active anchor,
- introduce some new term,
- drop some old term.

Repair is therefore a true rewrite, not append-only expansion.

The target runtime also keeps the runtime-only legality check

$$
\texttt{must\_have\_alias} \;\Longrightarrow\; U(n)\neq\varnothing.
$$

### 7.3 Crossover Legality

For `crossover_compose`, the query is built from shared anchors and donor terms. Legal donors must satisfy:

- positive reward above threshold,
- at least one shared anchor with the active node,
- support for at least one unmet must-have of the active node.

The target branch-lineage rule adds one more condition:

$$
a(n_{\text{child}})=a(n_{\text{parent}}),
$$

so crossover composes a repair hypothesis under an existing anchor lineage. It does not create a new truth lineage.

*Target owner anchor.* `runtime_budget.py`, `frontier_ops._validate_non_crossover_query_terms`, `search_ops.materialize_search_execution_plan`  
*Target trace anchor.* `max_query_terms`, `controller_decision`, `execution_plan`

## 8. Evidence Mining and Repair Ranking

### 8.1 Evidence Term Pool

From a completed CTS round, the system constructs a rewrite-only evidence pool from up to five top fit candidates. Evidence terms are mined from strong fields:

- title,
- project names,
- work summaries,
- work-experience summaries,
- search text.

Terms already present in the current query are rejected. Generic junk and topic drift are hard rejects.

Let \(\tau\) be a candidate repair term. Its accepted evidence score is

$$
S_{\text{ev}}(\tau)
=
S_{\text{sup}}(\tau)
+S_{\text{qual}}(\tau)
+S_{\text{field}}(\tau)
+B_{\text{must}}(\tau)
+B_{\text{anchor}}(\tau)
+B_{\text{pack}}(\tau)
-P_{\text{generic}}(\tau).
$$

The exact subscore shapes remain policy-owned, but the target intent is unchanged:

- stronger source support is better,
- higher-quality supporting candidates are better,
- title and project evidence outrank weaker fields,
- unmet must-have repair receives a privileged bonus,
- generic fragments are penalized.

The final accepted pool is still bounded and rewrite-only. Evidence terms are inputs to repair ranking; they do not directly define recall policy or override the root anchor.

### 8.2 GA-lite Repair Ranking

Let \(q^{(0)}\) be the controller draft query. GA-lite still performs a bounded local search by replacing a small number of non-anchor terms with evidence terms, under a capped legal population.

Each legal candidate \(q\) is scored by

$$
F(q)=
\alpha_A A(q)
+\alpha_M M(q)
+\alpha_C C(q)
+\alpha_P P(q)
-\alpha_L L(q)
-\alpha_R R(q),
$$

where:

- \(A(q)\) is anchor preservation,
- \(M(q)\) is must-have repair,
- \(C(q)\) is rewrite coherence,
- \(P(q)\) is provenance coherence,
- \(L(q)\) is query-length pressure,
- \(R(q)\) is redundancy and gratuitous churn.

The target default policy is priority-ordered rather than pseudo-precise:

$$
\alpha_A > \alpha_M > \alpha_C > \alpha_P,
$$

and both \(\alpha_L\) and \(\alpha_R\) must be materially stronger than the current baseline so that gratuitous change is rejected instead of merely downranked.

Operationally, the repair ranker prefers:

1. preserving anchor truth,
2. repairing unmet must-haves,
3. staying coherent with accepted evidence,
4. staying coherent with evidence provenance,
5. avoiding unnecessary query churn.

This section intentionally does not fix a new numeric weight table. Exact weights are a policy artifact, not part of the canonical target model.

*Target owner anchor.* `rewrite_evidence.py`, `frontier_ops._ga_lite_query_rewrite`, `frontier_ops._rewrite_fitness`  
*Target trace anchor.* `rewrite_term_pool`, `rewrite_choice_trace`

## 9. CTS Execution, Candidate Scoring, and Reviewer Evidence

### 9.1 CTS Execution

Each executed branch produces a CTS execution plan

$$
E(n)=\left(q_{\text{exec}},\; f_{\text{proj}},\; c_{\text{run}},\; m_{\text{target}}\right),
$$

where:

- \(q_{\text{exec}}\) is the final query,
- \(f_{\text{proj}}\) are projected hard constraints,
- \(c_{\text{run}}\) are runtime-only constraints such as must-have and negative keywords,
- \(m_{\text{target}}\) is the target new-candidate count.

CTS execution remains a retrieval substrate rather than a decision surface.

### 9.2 Candidate Scoring

Candidate scoring still fuses:

- rerank relevance,
- must-have match,
- preferred match,
- soft risk penalties,
- fit-gate checks for hard constraints.

The target scoring output remains permissive under absent structured fields unless a hard gate explicitly fails. Missing evidence alone does not imply rejection.

### 9.3 Reviewer Evidence Layer

The target system adds a reviewer evidence layer between scoring and finalization. This layer does not replace ranking; it translates ranked candidates into recruiter-decision surfaces.

The first target payload is

$$
\texttt{MustHaveEvidenceRow\_t}=
\left(\texttt{capability},\; \texttt{verdict},\; \texttt{evidence\_snippets},\; \texttt{source\_fields}\right),
$$

where

$$
\texttt{verdict}\in\{\texttt{explicit\_hit},\texttt{weak\_inference},\texttt{missing}\}.
$$

Its semantics are:

- `explicit_hit`: the must-have is directly supported by candidate text or trusted structured signals,
- `weak_inference`: the capability is only weakly implied and needs recruiter judgment,
- `missing`: no acceptable support is present in the reviewer layer.

The second target payload is

$$
\texttt{CandidateEvidenceCard\_t}=
\left(
\texttt{candidate\_id},
\texttt{review\_recommendation},
\texttt{must\_have\_matrix},
\texttt{preferred\_evidence},
\texttt{gap\_signals},
\texttt{risk\_signals},
\texttt{card\_summary}
\right),
$$

where

$$
\texttt{review\_recommendation}\in\{\texttt{advance},\texttt{hold},\texttt{reject}\}.
$$

The card is not a prose-only explanation. It is a structured reviewer surface with these meanings:

- `must_have_matrix`: ordered `MustHaveEvidenceRow_t` rows, each with machine-stable verdicts plus human-readable `evidence_summary`,
- `preferred_evidence`: positive but non-essential signals,
- `gap_signals`: explicit missing or weakly supported requirements, each with machine-stable `signal` and recruiter-facing `display_text`,
- `risk_signals`: recruiter-visible risks rather than raw ranking penalties alone, also carried as `signal + display_text`,
- `card_summary`: a compact review sentence grounded in the card.

The target `SearchScoringResult_t` therefore extends from rank-only output to reviewer-ready output by adding a candidate-evidence collection:

$$
\texttt{SearchScoringResult\_t}
\supset
\{\texttt{candidate\_evidence\_cards}\}.
$$

*Target owner anchor.* `search_ops.score_search_results`, `models.py`  
*Target trace anchor.* `scoring_result`, `explanation_candidate_ids`, `candidate_evidence_cards`

## 10. Final Candidate Presentation and Run Output

The final output layer no longer ends at ordered candidate ids. Finalization must produce reviewer-ready presentation payloads.

Let

$$
\Gamma^\star = \left[\gamma_1,\gamma_2,\ldots,\gamma_m\right]
$$

be the ordered list of final candidate evidence cards.

The active `SearchRunResult` is cards-first:

$$
\texttt{SearchRunResult}
=
\{\texttt{final\_candidate\_cards},\; \texttt{reviewer\_summary},\; \texttt{run\_summary},\; \texttt{stop\_reason}\}.
$$

The output semantics are:

- `final_candidate_cards` is the primary recruiter-facing output,
- `reviewer_summary` is a concise reviewer-oriented explanation of the final card set,
- `run_summary` remains the bounded-search runtime summary rather than a per-candidate judgment.

Final shortlist definition changes accordingly: a valid final result is a reviewer-ready candidate-card list rather than a ranked id list.

*Target owner anchor.* `runtime_ops.finalize_search_run`, `api.py`, `models.py`  
*Target trace anchor.* `final_result`, `final_result.json`, `bundle.json`

## 11. Reward, Frontier Update, and Stop Policy

### 11.1 Runtime Reward

Runtime reward continues to serve bounded frontier search rather than recruiter output presentation. Its inputs still come from:

- top-three quality delta,
- must-have gain,
- new-fit yield,
- novelty,
- usefulness,
- diversity,
- stability risk,
- hard-constraint violation,
- duplicate cost,
- page cost.

This layer remains search-control reward, not business evaluation.

### 11.2 Frontier Update

The target frontier update semantics change in one decisive way:

- creating a repair child must not close the root anchor that owns its lineage,
- every repair child must inherit `root_anchor_frontier_node_id`,
- non-root branches may still close on exhausted or low-gain outcomes,
- root anchors remain persistent selectable truth branches until terminal finalization.

Formally, if \(n_p\) is the parent and \(n_c\) the child created from it, then

$$
\beta(n_c)=\texttt{repair\_hypothesis}
\Longrightarrow
a(n_c)=a(n_p),
$$

and

$$
\beta(n_p)=\texttt{root\_anchor}
\Longrightarrow
\sigma(n_p)\neq\texttt{closed}
$$

solely because \(n_c\) was created.

### 11.3 Stop Policy

Root-anchor persistence changes terminal semantics. A target runtime can no longer rely on the old idea that the run ends simply because there are no open nodes left. Instead, stopping occurs when one of the following holds:

- budget is exhausted,
- controller stop is allowed and accepted under phase policy,
- no productive anchor path remains under the current stop guard.

The last case means: persistent anchors may remain structurally open, but every legal continuation under those anchors has fallen below the gain floor required by stop policy.

*Target owner anchor.* `runtime_ops.compute_node_reward_breakdown`, `runtime_ops.update_frontier_state`, `runtime_ops.evaluate_stop_condition`  
*Target trace anchor.* `reward_breakdown`, `frontier_state_after`, `effective_stop_guard`, `stop_reason`

## 12. Trace, Diagnostics, and Structural Invariants

Every search round must store, at minimum:

- frontier state before and after the round,
- controller context and decision,
- execution plan when search executes,
- execution result,
- scoring result,
- rewrite term pool,
- rewrite choice trace when repair ranking produces one,
- reward breakdown.

Run-level diagnostics must keep the current phased search diagnostics and extend them with session and business indicators.

### 12.1 Session and Business Metrics

The target run diagnostics retain the current phased metrics and add:

- `time_to_first_advance_round`,
- `pages_per_advance_candidate`,
- `advance_candidates_per_query`,
- `query_churn_rate`,
- `review_burden_candidate_count`,
- `review_burden_per_advance`.

These metrics use the following target semantics.

Let \(\Gamma_t\) denote the reviewer evidence cards produced by search round \(t\).

An advance candidate is defined by

$$
\texttt{advance\_candidate} \Longleftrightarrow
\texttt{review\_recommendation}=\texttt{advance}.
$$

Time to first advance round is

$$
t_{\text{first-adv}}=
\min \{t : \exists \gamma \in \Gamma_t \text{ with } \texttt{review\_recommendation}=\texttt{advance}\},
$$

or null when no advance candidate is produced.

Pages per advance candidate is

$$
\frac{\texttt{total\_pages\_fetched}}
{\max(1,\texttt{unique\_advance\_candidate\_count})}.
$$

Advance candidates per query is

$$
\frac{\texttt{unique\_advance\_candidate\_count}}
{\max(1,\texttt{search\_round\_count})}.
$$

For adjacent executed queries \(q_t\) and \(q_{t+1}\), query churn is the replacement ratio

$$
\chi_t=
\frac{|q_t \triangle q_{t+1}|}{\max(1,|q_t \cup q_{t+1}|)},
$$

and run-level query churn is the mean of \(\chi_t\) over executed adjacent search rounds.

Review burden is defined as the count of unique candidates that enter the reviewer evidence layer:

$$
\texttt{review\_burden\_candidate\_count}
=
\left|\bigcup_t \{\gamma.\texttt{candidate\_id} : \gamma \in \Gamma_t\}\right|.
$$

Review burden per advance is

$$
\frac{\texttt{review\_burden\_candidate\_count}}
{\max(1,\texttt{unique\_advance\_candidate\_count})}.
$$

### 12.2 Structural Invariants

The hard invariants of the target model are:

1. The same text-match predicate \(H\) must be used by:
   - coverage opportunity,
   - unmet must-have detection,
   - evidence topic-drift gating,
   - scoring-layer text matching,
   - reviewer-layer explicit evidence detection.
2. Round-0 query budget must equal the `explore` query budget.
3. Root anchor branches must remain persistent truth branches throughout the nonterminal run.
4. Every repair hypothesis must carry `root_anchor_frontier_node_id`.
5. Repair child creation must not close the owning root anchor.
6. Evidence terms are repair-only inputs; they are not direct recall policy.
7. Runtime reward and session/business eval are distinct objective layers.
8. `final_candidate_cards` is the only canonical final-result carrier.

Violation of any of these invariants makes the runtime self-inconsistent even if local subroutines still appear correct.

*Target owner anchor.* `prompt_surfaces.py`, `run_artifacts.py`, `controller_llm.py`, `runtime/orchestrator.py`  
*Target trace anchor.* `SearchRoundArtifact`, `SearchRunBundle.eval`, `eval.json`

## 13. Future Experiments

The current target model is still intentionally bounded. Deferred directions beyond this target revision include:

- exemplar or query-by-example bootstrap,
- recommendation-augmented search under first-party CTS,
- risk-aware probing budgets for non-first-party environments,
- stronger replay-driven tuning of repair-policy weights,
- capability ontology beyond the current shared lexical hit predicate.

These are future model changes beyond the current target spec revision.
