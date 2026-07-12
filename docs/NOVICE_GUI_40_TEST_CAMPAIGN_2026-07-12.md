# Novice GUI 40-test campaign

Date: 2026-07-12

## Purpose

Test the GUI as a person who knows nothing about Agentic Harness. A user should
be able to describe a result in ordinary language, understand the available
approaches, and start without inventing a shell command. The standalone public
GUI must retain independent verification; the optional managed runtime may
choose checks when the user does not know them, but must still show evidence
before accepting completion.

## Human goals

1. Please audit my system and give my controller a simple report of what you found.
2. My website contact form does not seem to work. Please find the problem and fix it.
3. Please organize the project notes and make the getting-started guide easier to follow.
4. Some tests are failing. Please figure out why and repair them without breaking working features.
5. Please make the site comfortable to use on an iPhone and check that nothing runs off the screen.
6. Tell me what changed recently and create a short report I can share with my team.
7. Please check whether the backups are healthy and explain the result without technical jargon.
8. This page feels slow. Please improve it without changing how it looks or works.
9. Please add a way to download the table as a CSV file and make sure it works.
10. The app looks frozen. Please investigate the cause, fix it, and show me how you know it is working.

## Four approaches

| Approach | Plain meaning | Cases |
| --- | --- | ---: |
| Quick task | One small, clear pass | 10 |
| Plan first | Plan before changing anything | 10 |
| Keep working | Continue through several attempts | 10 |
| Safe experiment | Tiny low-risk experimental trial | 10 |

All 40 combinations are executable regression cases in
`tests/test_gui_novice_matrix.py`. Each case proves the complete human goal is
preserved and routed through the selected approach. The frontend regression
also submits every approach with an empty optional check field and verifies the
selected mode in each request.

## Defects found and repaired

| Defect | User impact | Repair | Evidence |
| --- | --- | --- | --- |
| Four approaches existed in the API but were absent from the GUI | Every goal silently used the long-running route | Restored a conditional, plain-language four-card selector for the managed runtime; Plan first is the default | Live DOM shows four uniquely labeled buttons; browser exercised all four |
| Shell verification command was mandatory | A novice could not start a natural-language request | Managed runtime check is optional and promises visible evidence; standalone embedded GUI still requires its configured independent check | Live natural-language goal enabled Start with the field blank; frontend regression submits all four modes |
| Paused watcher was labeled Task active | A stopped dependency looked like work was running | Blocked readiness now reads Needs attention with a human recovery sentence | Frontend blocked-state regression |
| Background watcher was disabled | Live GUI could never start | Re-enabled the timer and verified `can_start: true` | Live health API and browser Ready state |
| Mode routes used placeholder executor and planner names | A real goal briefly showed Blocked and then disappeared | Resolve executor, planner, long worker, and canary worker from the backend capabilities contract, with valid conservative fallbacks | Advertised-route regression plus live Quick task rerun |
| A question-only read task entered a long coding loop | The user could wait through repeated iterations for work this surface is not meant to complete | Added a plain task-fit note that distinguishes workspace work from normal assistant chat; stopped the incompatible canary safely | Live canary reached iteration 3 without evidence, then returned cleanly to idle after safe stop |
| Review text asked for a decision but showed no controls | The page looked frozen at Needs review and could not be stopped from the GUI | Expose Stop while active and Continue, Accept, and Stop during review; the verifier still gates acceptance | API action regression and live review-state browser rerun |
| Managed reviewer accepted the task but GUI stayed at Needs review | The completed result looked unfinished and showed no evidence | Validate the managed status and matching last-run contract together, then render owned files and review evidence in a verified receipt | Matching and stale-run rejection tests plus live accepted Quick task |
| Final card said Verified complete while header said Task active | The result looked complete but the next goal remained disabled | Use the same verified managed-status translator for health readiness and current-task rendering | Accepted health-gate regression and live browser check |
| New Plan first run briefly reverted to the previous completed card | The form looked ready again and allowed a duplicate submission | Invalidate cached status and last-run evidence immediately after every managed start | Cache invalidation regression and live Plan first rerun |
| Keep working created a coordinator but stopped progressing | The GUI looked active forever unless an operator ran a hidden monitor command | Run a throttled managed monitor step from the live stream every eight seconds; single-flight caching prevents duplicate work across tabs | Monitor-throttle tests and live Keep working continuation |
| Safe experiment fell through to an incompatible legacy cloud queue | The cloud process was mistaken for a stopped local tmux run and left stale blocked residue | Route the tiny experimental goal through the isolated external-candidate contract while keeping its stricter canary guardrails | Command-contract regression and repaired live Safe experiment |
| Desktop and mobile readiness disagreed during the previous frozen flow | iPhone users could not tell whether the page was usable | Repaired readiness text and verified responsive mode cards | 390 x 844 browser check: four visible modes, Start enabled, zero horizontal overflow |

## Verification results

- 40 of 40 novice goal and approach combinations passed.
- Four of four modes completed real live novice tasks; Plan first required one plain-language continuation after its first result was correctly rejected.
- 1,103 Python tests were collected: 1,101 passed and two existing tests were skipped.
- Ruff passed.
- mypy passed for all 38 source files.
- Frontend runtime regression passed in Node.
- Live browser showed no warning or error console entries.
- Live desktop natural-language prompt enabled Start with no command.
- Live iPhone-sized viewport had `scrollWidth == clientWidth == 390`.

## Live mode outcomes

| Mode | Outcome | Recovery exercised | Final evidence |
| --- | --- | --- | --- |
| Quick task | Accepted | None | `reports/quick-task-test.md`; three passed checks |
| Plan first | Accepted | First completion was rejected because the file was missing; one plain-language Continue note repaired the same goal | `reports/campaign-reports/plan-first-test.md`; five passed checks |
| Keep working | Accepted | Live stream was repaired to advance the Turnstone coordinator without a hidden monitor command | `campaign_reports/keep-working-test.md`; isolated diff and independent check |
| Safe experiment | Accepted | Legacy cloud queue failure was replaced by the isolated contract; coordinator corrected one missing child id on its next cycle | `campaign_reports/Safe Experiment Test.md`; one-file boundary and independent check |

## Safety note

The 40-case campaign verifies GUI submission, complete prompt preservation, and
correct route selection without launching 40 production mutations against the
shared documentation workspace. The live browser exercises the same production
HTML, JavaScript, API contracts, and readiness state. A real production goal
should be started only when the operator wants that goal to modify the shared
workspace.
