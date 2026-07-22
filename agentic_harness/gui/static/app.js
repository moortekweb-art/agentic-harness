const STORAGE_KEY = "agentic-harness-gui-form";
const THEME_KEY = "agentic-harness-theme";
const TOKEN_KEY = "agentic-harness-gui-session-token";
const ICON_PREFIX = "#icon-";
const API_TIMEOUT_MS = 20000;
// Managed local-model profile changes can include a guarded model swap and startup probe.
const START_TIMEOUT_MS = 360000;
const MODES_REFRESH_MIN_INTERVAL_MS = 10000;
const DEFAULT_PUBLIC_STRATEGY = "plan";
const DEFAULT_MANAGED_MODE = "mode1";
const DEFAULT_EXECUTION_EFFORT = "standard";
const AUTOMATIC_PROFILE_KEY = "automatic";
const EFFORT_LABELS_BY_BUDGET = Object.freeze({
  small: "Quick",
  balanced: "Standard",
  full: "Thorough",
  tiny: "Experiment",
});

const STATUS_ICONS = Object.freeze({
  ready: "circle-check",
  starting: "loader-circle",
  working: "loader-circle",
  checking: "loader-circle",
  stopping: "loader-circle",
  needs_review: "circle-alert",
  needs_attention: "octagon-alert",
  done: "circle-check",
  blocked: "octagon-alert",
  stopped: "circle-stop",
});

const state = {
  mode: DEFAULT_PUBLIC_STRATEGY,
  modeDefault: DEFAULT_PUBLIC_STRATEGY,
  modeKind: "strategy",
  modesLoaded: false,
  modesError: "",
  lastModesRefreshAt: 0,
  activeView: "home",
  modes: [],
  routes: [],
  route: "",
  routeDefault: "",
  efforts: [],
  effort: DEFAULT_PUBLIC_STRATEGY,
  effortDefault: DEFAULT_PUBLIC_STRATEGY,
  executionProfiles: [],
  executionProfile: AUTOMATIC_PROFILE_KEY,
  executionProfileDefault: "",
  supervision: "none",
  busy: false,
  setupBusy: false,
  authToken: "",
  authPromptPromise: null,
  setupPrompted: false,
  readiness: {},
  readinessSignature: "",
  taskAvailabilityState: "",
  setup: null,
  currentTask: null,
  liveTask: null,
  viewingHistoryId: "",
  undoStack: [],
  redoStack: [],
  socket: null,
  pollTimer: null,
  reconnectDelay: 1000,
  refreshes: {},
  pendingStartObjective: "",
  lastRenderedTaskId: "",
  formReconciled: false,
  restoredDraftVersion: 0,
  providerTemplates: [],
  localModelDetection: null,
  specificationReviewBinding: null,
};

const byId = (id) => document.getElementById(id);
const els = {
  homeTab: byId("homeTab"),
  tasksTab: byId("tasksTab"),
  historyTab: byId("historyTab"),
  homeView: byId("homeView"),
  tasksView: byId("tasksView"),
  historyView: byId("historyView"),
  settingsView: byId("settingsView"),
  health: byId("health"),
  healthText: byId("healthText"),
  healthIcon: byId("healthIcon"),
  setupButton: byId("setupButton"),
  themeButton: byId("themeButton"),
  themeIcon: byId("themeIcon"),
  shortcutsButton: byId("shortcutsButton"),
  workspacePath: byId("workspacePath"),
  executionSummary: byId("executionSummary"),
  demoCallout: byId("demoCallout"),
  demoTitle: byId("demoTitle"),
  demoSummary: byId("demoSummary"),
  demoButton: byId("demoButton"),
  demoButtonLabel: byId("demoButtonLabel"),
  demoSetupButton: byId("demoSetupButton"),
  objectiveLabel: byId("objectiveLabel"),
  objectiveHint: byId("objectiveHint"),
  objective: byId("objective"),
  modeSection: byId("modeSection"),
  modeSelect: byId("modeSelect"),
  modes: byId("modes"),
  advancedModes: byId("advancedModes"),
  advancedModeDetails: byId("advancedModeDetails"),
  modeHelp: byId("modeHelp"),
  effortRecommendation: byId("effortRecommendation"),
  expectationAvailability: byId("expectationAvailability"),
  expectationSummary: byId("expectationSummary"),
  expectationLocation: byId("expectationLocation"),
  expectationModel: byId("expectationModel"),
  expectationPlanner: byId("expectationPlanner"),
  expectationExecutor: byId("expectationExecutor"),
  expectationMutation: byId("expectationMutation"),
  expectationVerification: byId("expectationVerification"),
  expectationMaturity: byId("expectationMaturity"),
  expectationDetails: byId("expectationDetails"),
  routeSection: byId("routeSection"),
  routeSelect: byId("routeSelect"),
  routes: byId("routes"),
  routeRecommendation: byId("routeRecommendation"),
  modelProfileSection: byId("modelProfileSection"),
  modelProfileSelect: byId("modelProfileSelect"),
  modelProfiles: byId("modelProfiles"),
  advisorySupervisionSection: byId("advisorySupervisionSection"),
  advisorySupervision: byId("advisorySupervision"),
  advisorySupervisionText: byId("advisorySupervisionText"),
  approachSection: byId("approachSection"),
  candidateCount: byId("candidateCount"),
  safeAreas: byId("safeAreas"),
  accessSummary: byId("accessSummary"),
  checks: byId("checks"),
  verificationDetails: byId("verificationDetails"),
  verificationSummary: byId("verificationSummary"),
  verificationLabel: byId("verificationLabel"),
  verificationHelp: byId("verificationHelp"),
  startButton: byId("startButton"),
  startHelp: byId("startHelp"),
  checkButton: byId("checkButton"),
  recoveryCard: byId("recoveryCard"),
  recoveryTitle: byId("recoveryTitle"),
  recoverySummary: byId("recoverySummary"),
  recoveryContinueButton: byId("recoveryContinueButton"),
  recoveryStopButton: byId("recoveryStopButton"),
  recoveryOpenTaskButton: byId("recoveryOpenTaskButton"),
  recoveryStatus: byId("recoveryStatus"),
  statusLabel: byId("statusLabel"),
  statusIndicator: byId("statusIndicator"),
  statusIcon: byId("statusIcon"),
  summary: byId("summary"),
  taskGuide: byId("taskGuide"),
  taskGuideEyebrow: byId("taskGuideEyebrow"),
  taskGuideTitle: byId("taskGuideTitle"),
  taskGuideBody: byId("taskGuideBody"),
  taskGuideExplanation: byId("taskGuideExplanation"),
  taskGuideNext: byId("taskGuideNext"),
  taskGuideCounts: byId("taskGuideCounts"),
  taskGuideFiles: byId("taskGuideFiles"),
  taskGuideChecks: byId("taskGuideChecks"),
  taskGuideArtifacts: byId("taskGuideArtifacts"),
  progressGroup: byId("progressGroup"),
  progressTrack: byId("progressTrack"),
  progressValue: byId("progressValue"),
  progressBar: byId("progressBar"),
  currentSubgoal: byId("currentSubgoal"),
  checkpoint: byId("checkpoint"),
  workApproachValue: byId("workApproachValue"),
  attemptsValue: byId("attemptsValue"),
  taskContext: byId("taskContext"),
  returnToCurrentButton: byId("returnToCurrentButton"),
  currentCard: byId("currentCard"),
  continueButton: byId("continueButton"),
  continueButtonLabel: byId("continueButtonLabel"),
  approveSpecButton: byId("approveSpecButton"),
  approveSpecButtonLabel: byId("approveSpecButtonLabel"),
  acceptButton: byId("acceptButton"),
  acceptButtonLabel: byId("acceptButtonLabel"),
  stopButton: byId("stopButton"),
  stopButtonLabel: byId("stopButtonLabel"),
  conversationSection: byId("conversationSection"),
  conversationList: byId("conversationList"),
  messageForm: byId("messageForm"),
  messageInput: byId("messageInput"),
  messageButton: byId("messageButton"),
  messageHint: byId("messageHint"),
  planList: byId("planList"),
  requirementsList: byId("requirementsList"),
  eventTimeline: byId("eventTimeline"),
  finalResult: byId("finalResult"),
  completedDetails: byId("completedDetails"),
  finalLabel: byId("finalLabel"),
  finalReason: byId("finalReason"),
  finalWorkerClaimLabel: byId("finalWorkerClaimLabel"),
  finalWorkerClaim: byId("finalWorkerClaim"),
  finalAttempts: byId("finalAttempts"),
  finalRetries: byId("finalRetries"),
  finalChangedFiles: byId("finalChangedFiles"),
  finalVerification: byId("finalVerification"),
  finalRemaining: byId("finalRemaining"),
  workDetailGrid: byId("workDetailGrid"),
  activitySection: byId("activitySection"),
  changedFilesEvidence: byId("changedFilesEvidence"),
  verificationEvidence: byId("verificationEvidence"),
  artifactsEvidence: byId("artifactsEvidence"),
  changedFiles: byId("changedFiles"),
  verification: byId("verification"),
  artifacts: byId("artifacts"),
  historySearch: byId("historySearch"),
  historyStatusFilter: byId("historyStatusFilter"),
  historyRouteFilter: byId("historyRouteFilter"),
  historyEmpty: byId("historyEmpty"),
  historyList: byId("historyList"),
  exportButton: byId("exportButton"),
  exportButtonLabel: byId("exportButtonLabel"),
  advancedDetails: byId("advancedDetails"),
  statusUpdated: byId("statusUpdated"),
  shortcutsDialog: byId("shortcutsDialog"),
  setupForm: byId("setupForm"),
  setupJourney: byId("setupJourney"),
  setupJourneyImage: byId("setupJourneyImage"),
  setupJourneySummary: byId("setupJourneySummary"),
  setupStepChoose: byId("setupStepChoose"),
  setupStepConnect: byId("setupStepConnect"),
  setupStepVerify: byId("setupStepVerify"),
  closeSetupButton: byId("closeSetupButton"),
  saveSetupButton: byId("saveSetupButton"),
  managedSettings: byId("managedSettings"),
  managedSettingsSummary: byId("managedSettingsSummary"),
  managedWorkspace: byId("managedWorkspace"),
  managedExecution: byId("managedExecution"),
  managedVerification: byId("managedVerification"),
  configurationError: byId("configurationError"),
  configurationErrorText: byId("configurationErrorText"),
  editableSettings: byId("editableSettings"),
  executionChoice: byId("executionChoice"),
  executionDisclosure: byId("executionDisclosure"),
  codingAgentFields: byId("codingAgentFields"),
  codingAgentChoice: byId("codingAgentChoice"),
  codingAgentConnectionResult: byId("codingAgentConnectionResult"),
  providerFields: byId("providerFields"),
  providerPreset: byId("providerPreset"),
  providerPresetHelp: byId("providerPresetHelp"),
  localModelRequirement: byId("localModelRequirement"),
  localModelDetectionRow: byId("localModelDetectionRow"),
  localModelDetection: byId("localModelDetection"),
  localModelGuide: byId("localModelGuide"),
  detectedModelChoice: byId("detectedModelChoice"),
  useDetectedModelButton: byId("useDetectedModelButton"),
  checkLocalModelsButton: byId("checkLocalModelsButton"),
  providerEndpoint: byId("providerEndpoint"),
  providerModel: byId("providerModel"),
  providerApiKeyEnv: byId("providerApiKeyEnv"),
  providerApiKey: byId("providerApiKey"),
  manualConnectionDetails: byId("manualConnectionDetails"),
  connectionResult: byId("connectionResult"),
  remoteDataRow: byId("remoteDataRow"),
  confirmRemoteData: byId("confirmRemoteData"),
  verificationCommand: byId("verificationCommand"),
  assuranceMode: byId("assuranceMode"),
  automaticCheckLabel: byId("automaticCheckLabel"),
  automaticCheckDetail: byId("automaticCheckDetail"),
  maxCycles: byId("maxCycles"),
  maxMinutes: byId("maxMinutes"),
  maxTokens: byId("maxTokens"),
  maxProviderCalls: byId("maxProviderCalls"),
  maxToolCalls: byId("maxToolCalls"),
  setupError: byId("setupError"),
  setupDemoButton: byId("setupDemoButton"),
  continueDialog: byId("continueDialog"),
  continueForm: byId("continueForm"),
  closeContinueButton: byId("closeContinueButton"),
  continueFeedback: byId("continueFeedback"),
  specificationDialog: byId("specificationDialog"),
  specificationForm: byId("specificationForm"),
  specificationTitle: byId("specificationTitle"),
  specificationHelp: byId("specificationHelp"),
  specificationRequirements: byId("specificationRequirements"),
  approveSpecificationSubmit: byId("approveSpecificationSubmit"),
  closeSpecificationButton: byId("closeSpecificationButton"),
  previewDialog: byId("previewDialog"),
  previewTitle: byId("previewTitle"),
  previewContent: byId("previewContent"),
};

const routeUnavailableReasons = document.createElement("div");
routeUnavailableReasons.id = "routeUnavailableReasons";
routeUnavailableReasons.className = "mobile-unavailable-reasons";
routeUnavailableReasons.setAttribute("role", "note");
routeUnavailableReasons.setAttribute("aria-label", "Unavailable execution routes");
routeUnavailableReasons.hidden = true;
els.routeSection.append(routeUnavailableReasons);

function iconHref(name) {
  return `${ICON_PREFIX}${name}`;
}

function iconMarkup(name) {
  return `<svg class="icon" aria-hidden="true"><use href="${iconHref(name)}"></use></svg>`;
}

async function api(path, options = {}, retry = true, timeoutMs = API_TIMEOUT_MS) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (state.authToken) headers.set("Authorization", `Bearer ${state.authToken}`);
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timeout = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  let response;
  try {
    response = await fetch(path, { ...options, headers, signal: controller?.signal });
  } catch (error) {
    if (controller?.signal.aborted) {
      throw new Error("The server took too long to respond. Try Refresh; your task state is preserved.");
    }
    throw error;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
  if (response.status === 401 && retry) {
    clearAuthToken();
    const entered = await showTokenDialog();
    if (entered) {
      state.authToken = entered;
      sessionStorage.setItem(TOKEN_KEY, entered);
      return api(path, options, false, timeoutMs);
    }
  }
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    throw new Error(
      payload.error || (response.status === 401 ? "Authorization required." : `Request failed (${response.status})`),
    );
  }
  return payload;
}

function linesFrom(field) {
  return field.value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function showView(view, { focus = false } = {}) {
  const requested = ["home", "tasks", "history", "settings"].includes(view) ? view : "home";
  state.activeView = requested;
  const rows = [
    { name: "home", tab: els.homeTab, panel: els.homeView },
    { name: "tasks", tab: els.tasksTab, panel: els.tasksView },
    { name: "history", tab: els.historyTab, panel: els.historyView },
    { name: "settings", tab: els.setupButton, panel: els.settingsView },
  ];
  rows.forEach(({ name, tab, panel }) => {
    const active = name === requested;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    panel.hidden = !active;
  });
  if (focus) {
    const target = requested === "home"
      ? els.objective
      : requested === "history"
        ? els.historySearch
        : rows.find((row) => row.name === requested)?.panel;
    if (target && typeof target.focus === "function") target.focus();
  }
}

function handlePrimaryTabKeydown(event) {
  const tabs = [
    { name: "home", tab: els.homeTab },
    { name: "tasks", tab: els.tasksTab },
    { name: "history", tab: els.historyTab },
    { name: "settings", tab: els.setupButton },
  ];
  const current = tabs.findIndex(({ tab }) => tab === event.currentTarget);
  if (current < 0) return;
  let next = current;
  if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
  else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.length - 1;
  else return;
  event.preventDefault();
  showView(tabs[next].name);
  tabs[next].tab.focus();
}

function modePresentation(mode) {
  const effortLabel = state.efforts.includes(mode)
    ? EFFORT_LABELS_BY_BUDGET[mode?.budget_profile]
    : "";
  return {
    label: mode?.effort_label || effortLabel || mode?.friendly_name || mode?.label || mode?.title || "Automatic",
    description: mode?.short_purpose || mode?.summary || mode?.best_for || mode?.description || "Uses the configured task workflow.",
  };
}

function optionIsVisible(option) {
  return Boolean(option) && option.hidden !== true;
}

function optionIsAvailable(option) {
  return optionIsVisible(option) && option.available !== false && option.enabled !== false;
}

function optionIsLab(option) {
  const maturity = String(option?.maturity || "").toLowerCase();
  return option?.labs === true
    || option?.experimental === true
    || maturity.includes("canary")
    || maturity.includes("experimental");
}

function normalizeOptions(value) {
  return Array.isArray(value)
    ? value.filter((option) => option && typeof option === "object" && option.key)
    : [];
}

function availableSelection(options, current, preferred, { preserveUnavailable = false } = {}) {
  if (preserveUnavailable && options.some((option) => option.key === current && optionIsVisible(option))) return current;
  if (preserveUnavailable && options.some((option) => option.key === preferred && optionIsVisible(option))) return preferred;
  if (options.some((option) => option.key === current && optionIsAvailable(option))) return current;
  if (options.some((option) => option.key === preferred && optionIsAvailable(option))) return preferred;
  return options.find((option) => option.recommended === true && optionIsAvailable(option))?.key
    || options.find(optionIsAvailable)?.key
    || "";
}

function managedRouteSelection(options, current, preferred) {
  // A saved user choice or the backend's explicit default may remain selected
  // while unavailable so the UI can explain why it cannot start. A cloud or
  // mixed route always requires a user selection; it is never an implicit
  // fallback merely because the local route is unavailable.
  if (options.some((option) => option.key === current && optionIsVisible(option))) return current;
  const preferredRoute = options.find((option) => option.key === preferred && optionIsVisible(option));
  const preferredLocation = String(preferredRoute?.data_location || "").toLowerCase();
  const preferredNetwork = String(preferredRoute?.network_scope || "").toLowerCase();
  const sendsOffLocalLane = preferredLocation.includes("cloud")
    || ["cloud", "mixed", "external"].includes(preferredNetwork);
  if (preferredRoute && !sendsOffLocalLane) return preferred;
  return "";
}

function selectedRoute() {
  return state.routes.find((route) => route.key === state.route) || null;
}

function selectedEffort() {
  return state.efforts.find((effort) => effort.key === state.effort) || null;
}

function selectedExecutionProfile() {
  if (state.executionProfile === AUTOMATIC_PROFILE_KEY) return null;
  return state.executionProfiles.find((profile) => profile.key === state.executionProfile) || null;
}

function defaultExecutionProfile() {
  return state.executionProfiles.find((profile) => profile.key === state.executionProfileDefault) || null;
}

function routeUsesExecutionProfiles(route = selectedRoute()) {
  if (!usesHumanModes() || !route) return false;
  const supportsProfiles = route.supports_execution_profiles === true
    || route.execution_profiles_supported === true;
  const isLocalRoute = route.local_only === true
    || route.uses_local_node1 === true
    || ["local", "local_node1"].includes(String(route.data_location || "").toLowerCase());
  return supportsProfiles && isLocalRoute;
}

function routeSupportsGlmSupervision(route = selectedRoute()) {
  return Boolean(route) && (route.route_id === "local-build" || route.key === "mode1");
}

function selectedExecutionOption() {
  return usesHumanModes() ? selectedRoute() : selectedEffort();
}

function humanizeFact(value, fallback = "Automatic") {
  if (Array.isArray(value)) {
    const items = value.map((item) => humanizeFact(item, "")).filter(Boolean);
    return items.length ? items.join(", ") : fallback;
  }
  if (value && typeof value === "object") {
    return humanizeFact(value.label || value.summary || value.name || value.mode, fallback);
  }
  const text = String(value || "").trim();
  if (!text) return fallback;
  return text.includes("_")
    ? text.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase())
    : text;
}

function executionLocation(option) {
  if (option?.location_label) return option.location_label;
  if (option?.local_only === true) return "Local only";
  if (option?.type === "coding_agent") return "Through an installed coding app";
  const locations = {
    local_node1: "Local Node1",
    mixed_local_cloud: "Local + cloud",
    cloud_provider: "Cloud provider",
    local: "Local",
    cloud_and_local: "Local + cloud",
    cloud: "Cloud provider",
    device: "This computer",
    private_network: "Private network",
  };
  return locations[option?.data_location]
    || locations[option?.network_scope]
    || humanizeFact(option?.data_location || option?.network_scope, "Managed automatically");
}

function readinessPresentation(readiness = state.readiness) {
  const known = typeof readiness?.can_start === "boolean" || Boolean(readiness?.state);
  const ready = readiness?.can_start === true;
  const needsSetup = [
    "setup_required",
    "credential_required",
    "verification_required",
    "connection_test_required",
  ].includes(readiness?.state);
  const blocked = ["blocked", "needs_attention", "configuration_error"].includes(readiness?.state);
  return {
    known,
    ready,
    needsSetup,
    blocked,
    label: !known
      ? "Checking…"
      : ready
        ? "Ready"
        : needsSetup
          ? "Setup needed"
          : blocked
            ? "Needs attention"
            : "Task active",
    summary: readiness?.next_action || readiness?.summary || "",
  };
}

function plainManagedRouteSummary(route) {
  const location = String(route?.data_location || "").toLowerCase();
  const network = String(route?.network_scope || "").toLowerCase();
  if (location.includes("mixed") || network === "mixed") {
    return "Planning and execution will use both local and configured cloud services.";
  }
  if (location.includes("cloud") || network === "cloud" || network === "external") {
    return "The selected project scope will use the configured cloud route.";
  }
  if (location.includes("local") || network.includes("local") || route?.local_only === true) {
    return "Work will use the configured local execution lane.";
  }
  return "The connected installation will manage where this route runs.";
}

function embeddedAssistantSummary(worker) {
  if (worker?.type === "coding_agent") return "your installed coding app";
  if (worker?.type === "model_agent") return "your connected AI model";
  return "your connected assistant";
}

function focusChoice(container, key) {
  const card = Array.from(container?.children || [])
    .find((candidate) => candidate?.dataset?.choiceKey === key);
  if (card && typeof card.focus === "function") card.focus();
}

function executionMutation(option) {
  const mutation = String(option?.mutation || "").toLowerCase();
  if (mutation === "audit" || mutation === "audit_only") return "Read-only audit";
  if (mutation.includes("canary")) return "Bounded canary changes";
  return mutation ? "Can change files" : "As requested";
}

function technicalModeLabel(option) {
  const explicit = String(option?.technical_label || option?.technical_mode || "").trim();
  if (explicit) return explicit;
  const number = option?.mode_number ?? option?.number;
  return number !== undefined && number !== null && String(number).trim()
    ? `Mode ${number}`
    : "";
}

function createChoiceCard(option, selected, onSelect, { technical = false } = {}) {
  const presentation = modePresentation(option);
  const available = optionIsAvailable(option);
  const card = document.createElement("button");
  card.type = "button";
  card.dataset.choiceKey = option.key;
  card.className = `mode-card${available ? "" : " unavailable"}`;
  card.disabled = !available;
  card.setAttribute("aria-pressed", String(option.key === selected));
  card.setAttribute("aria-disabled", String(!available));
  card.setAttribute("aria-label", `${presentation.label}. ${presentation.description}${available ? "" : `. ${option.disabled_reason || "Unavailable"}`}`);

  const titleRow = document.createElement("span");
  titleRow.className = "mode-card-title";
  const title = document.createElement("strong");
  title.textContent = presentation.label;
  titleRow.append(title);
  const technicalLabel = technical ? technicalModeLabel(option) : "";
  if (technicalLabel) {
    const badge = document.createElement("small");
    badge.className = "technical-mode-badge";
    badge.textContent = technicalLabel;
    titleRow.append(badge);
  }
  const description = document.createElement("span");
  description.textContent = presentation.description;
  card.append(titleRow, description);
  const noteText = !available
    ? option.disabled_reason || "Not available on this installation."
    : option.caution || option.policy || "";
  if (noteText) {
    const note = document.createElement("small");
    note.className = "mode-card-note";
    note.textContent = noteText;
    card.append(note);
  }
  if (available) card.addEventListener("click", onSelect);
  return card;
}

function appendChoiceOption(select, option, selected, { technical = false, lab = false } = {}) {
  const presentation = modePresentation(option);
  const row = document.createElement("option");
  row.value = option.key;
  row.disabled = !optionIsAvailable(option);
  row.selected = option.key === selected;
  const badge = technical ? technicalModeLabel(option) : "";
  row.textContent = `${presentation.label}${lab ? " · Labs" : ""}${badge ? ` · ${badge}` : ""}${row.disabled ? " · unavailable" : ""}`;
  select.append(row);
}

function renderUnavailableRouteReasons(routes) {
  routeUnavailableReasons.replaceChildren();
  routes.filter((route) => !optionIsAvailable(route)).forEach((route) => {
    const reason = document.createElement("p");
    reason.textContent = `${modePresentation(route).label}: ${route.disabled_reason || "Not available on this installation."}`;
    routeUnavailableReasons.append(reason);
  });
  routeUnavailableReasons.hidden = routeUnavailableReasons.children.length === 0;
}

function renderExpectationSummary() {
  const route = selectedRoute();
  const effort = selectedEffort();
  const profile = selectedExecutionProfile();
  const option = route || effort || {};
  const setupWorker = state.setup?.worker || {};
  const managed = usesHumanModes();
  const modesLoadFailed = Boolean(state.modesError);
  const executionChoicesPending = !state.modesLoaded;
  const routeMissing = managed && !route;
  const effortMissing = !effort;
  const selectionMissing = routeMissing || effortMissing;
  const executionAvailable = !executionChoicesPending
    && !selectionMissing
    && optionIsAvailable(option);
  const readiness = readinessPresentation();
  const ready = !modesLoadFailed && executionAvailable && readiness.ready;
  const defaultProfile = defaultExecutionProfile();
  const setupConfigured = managed || state.setup?.configured === true;
  const profileLabel = managed
    ? routeMissing
      ? "Choose a route"
      : routeUsesExecutionProfiles(route)
        ? profile?.label
          || (state.executionProfiles.length
            ? (defaultProfile ? `Automatic (${modePresentation(defaultProfile).label})` : "Automatic")
            : "Managed by this route")
        : "Managed by this route"
    : !setupConfigured
      ? "Connect AI in Settings"
      : setupWorker.type === "coding_agent"
        ? `Coding app · ${setupWorker.label || "Configured app"}`
        : setupWorker.type === "model_agent"
          ? setupWorker.model || "Configured AI model"
          : setupWorker.label || "Configured assistant";
  const effortLabel = modePresentation(effort).label;
  const routeLabel = route ? modePresentation(route).label : "Configured execution";
  const unavailableReason = !optionIsAvailable(option) ? option?.disabled_reason || "" : "";
  const checking = !modesLoadFailed && (executionChoicesPending || !readiness.known);
  const availabilityLabel = modesLoadFailed
    ? "Needs refresh"
    : executionChoicesPending || !readiness.known
      ? "Checking…"
      : selectionMissing || !executionAvailable
        ? "Unavailable"
        : readiness.ready
          ? "Ready"
          : readiness.label;

  els.expectationAvailability.textContent = availabilityLabel;
  els.expectationAvailability.className = ready || checking
    ? "expectation-ready"
    : "expectation-ready unavailable";
  els.expectationSummary.textContent = modesLoadFailed
    ? `${state.modesError} Choose Refresh to check the execution routes again.`
    : executionChoicesPending
      ? "Confirming the available execution choices before this task can start."
      : routeMissing
        ? "No execution route was selected automatically. Choose an available route to continue."
        : effortMissing
          ? "No task effort is available on this installation."
          : unavailableReason
            ? unavailableReason
            : !readiness.known
              ? "Confirming whether this project is ready to start another task."
              : !readiness.ready
                ? readiness.summary || "Finish the current setup or task before starting another one."
                : managed
                  ? `${routeLabel} will use ${effortLabel.toLowerCase()} effort. ${plainManagedRouteSummary(route)}${state.supervision === "glm-5.2" ? " GLM-5.2 advisory supervision will be started and verified." : ""}`
                  : `${effortLabel} uses ${embeddedAssistantSummary(setupWorker)} and independent verification.`;
  els.expectationLocation.textContent = executionChoicesPending
    ? "Checking…"
    : routeMissing
      ? "Choose a route"
      : route
        ? executionLocation(route)
        : !setupConfigured
          ? "Connect AI in Settings"
          : executionLocation(setupWorker);
  els.expectationModel.textContent = profileLabel;
  const modelFactLabel = managed
    ? routeUsesExecutionProfiles(route) ? "Model" : "Execution"
    : setupWorker.type === "model_agent" ? "Model" : "Assistant";
  const modelLabelElement = els.expectationModel.previousElementSibling;
  if (modelLabelElement) modelLabelElement.textContent = modelFactLabel;
  els.expectationModel.setAttribute("aria-label", `${modelFactLabel}: ${profileLabel}`);
  els.expectationPlanner.textContent = humanizeFact(route?.planner || setupWorker.planner);
  els.expectationExecutor.textContent = humanizeFact(route?.executor || route?.worker || setupWorker.executor || setupWorker.label);
  els.expectationMutation.textContent = routeMissing
    ? "Choose a route"
    : !managed && !setupConfigured
      ? "Available after setup"
      : !managed && (effort?.requires_scope === true || effort?.requires_enforced_scope === true)
        ? "Limited to selected files"
        : route ? executionMutation(route) : "Can change project files";
  const judgmentTask = /\b(audit|assess|assessment|review|rate|rating|recommend|report|explain)\b/i
    .test(els.objective.value.trim());
  els.expectationVerification.textContent = judgmentTask
    ? "You will review the result"
    : "Automatic checks when possible";
  els.expectationVerification.title = judgmentTask
    ? "Judgment tasks stop safely so you can approve the result or ask for changes."
    : "The harness will use independent checks when it can establish a reliable verifier; otherwise it will ask for your review.";
  els.expectationMaturity.textContent = humanizeFact(route?.maturity, managed ? "Supported" : "Production");
}

function renderModeControls() {
  if (state.routes.length) {
    state.route = managedRouteSelection(state.routes, state.route, state.routeDefault);
  }
  if (state.efforts.length) {
    state.effort = availableSelection(state.efforts, state.effort, state.effortDefault);
  }
  if (state.executionProfiles.length && state.executionProfile !== AUTOMATIC_PROFILE_KEY) {
    state.executionProfile = availableSelection(
      state.executionProfiles,
      state.executionProfile,
      state.executionProfileDefault,
    ) || AUTOMATIC_PROFILE_KEY;
  }
  state.mode = usesHumanModes() ? state.route : state.effort;

  els.modes.replaceChildren();
  els.advancedModes.replaceChildren();
  els.modeSelect.replaceChildren();
  const regularEfforts = state.efforts.filter((effort) => optionIsVisible(effort) && !optionIsLab(effort));
  const labEfforts = usesHumanModes()
    ? []
    : state.efforts.filter((effort) => optionIsVisible(effort) && optionIsLab(effort));
  regularEfforts.forEach((effort) => {
    const choose = () => {
      state.effort = effort.key;
      renderModeControls();
      focusChoice(els.modes, effort.key);
      pushUndo();
      persistForm();
      updateStartButton();
    };
    els.modes.append(createChoiceCard(effort, state.effort, choose));
    appendChoiceOption(els.modeSelect, effort, state.effort);
  });
  labEfforts.forEach((effort) => appendChoiceOption(
    els.modeSelect,
    effort,
    state.effort,
    { lab: true },
  ));
  if ([...regularEfforts, ...labEfforts].some((effort) => effort.key === state.effort)) {
    els.modeSelect.value = state.effort;
  }
  const effortPresentation = modePresentation(selectedEffort());
  els.modeHelp.textContent = `${effortPresentation.label}: ${effortPresentation.description}`;
  const recommendedEffort = state.efforts.find((effort) => effort.recommended === true && optionIsAvailable(effort));
  els.effortRecommendation.textContent = recommendedEffort
    ? `${modePresentation(recommendedEffort).label} is recommended.`
    : "Choose the effort that fits this task.";

  els.routes.replaceChildren();
  els.routeSelect.replaceChildren();
  if (!state.route) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Choose an execution route";
    placeholder.disabled = true;
    placeholder.selected = true;
    els.routeSelect.append(placeholder);
  }
  const visibleRoutes = state.routes.filter(optionIsVisible);
  const regularRoutes = visibleRoutes.filter((route) => !optionIsLab(route));
  const labRoutes = visibleRoutes.filter(optionIsLab);
  regularRoutes.forEach((route) => {
    const choose = () => {
      state.route = route.key;
      renderModeControls();
      focusChoice(els.routes, route.key);
      pushUndo();
      persistForm();
      updateStartButton();
    };
    els.routes.append(createChoiceCard(route, state.route, choose));
    appendChoiceOption(els.routeSelect, route, state.route);
  });
  labRoutes.forEach((route) => appendChoiceOption(
    els.routeSelect,
    route,
    state.route,
    { technical: true, lab: true },
  ));
  if ([...regularRoutes, ...labRoutes].some((route) => route.key === state.route)) {
    els.routeSelect.value = state.route;
  }
  els.routeSection.hidden = regularRoutes.length === 0;
  renderUnavailableRouteReasons(regularRoutes);
  const recommendedRoute = regularRoutes.find((route) => route.recommended === true && optionIsAvailable(route));
  els.routeRecommendation.textContent = recommendedRoute
    ? `${modePresentation(recommendedRoute).label} is recommended.`
    : "";

  [...labEfforts, ...labRoutes].forEach((option) => {
    const isRoute = state.routes.includes(option);
    const choose = () => {
      if (isRoute) state.route = option.key;
      else state.effort = option.key;
      renderModeControls();
      focusChoice(els.advancedModes, option.key);
      pushUndo();
      persistForm();
      updateStartButton();
    };
    els.advancedModes.append(createChoiceCard(
      option,
      isRoute ? state.route : state.effort,
      choose,
      { technical: isRoute },
    ));
  });
  els.advancedModeDetails.hidden = els.advancedModes.children.length === 0;
  els.approachSection.hidden = usesHumanModes();
  if (usesHumanModes()) els.candidateCount.value = "1";

  els.modelProfiles.replaceChildren();
  els.modelProfileSelect.replaceChildren();
  if (state.executionProfiles.length) {
    const defaultProfile = defaultExecutionProfile();
    const automatic = {
      key: AUTOMATIC_PROFILE_KEY,
      label: "Automatic",
      summary: defaultProfile
        ? `Use the installation default (${modePresentation(defaultProfile).label}).`
        : "Let this installation choose its supported default model.",
      available: true,
    };
    [automatic, ...state.executionProfiles.filter(optionIsVisible)].forEach((profile) => {
      const choose = () => {
        state.executionProfile = profile.key;
        renderModeControls();
        focusChoice(els.modelProfiles, profile.key);
        pushUndo();
        persistForm();
      };
      els.modelProfiles.append(createChoiceCard(profile, state.executionProfile, choose));
      appendChoiceOption(els.modelProfileSelect, profile, state.executionProfile);
    });
    els.modelProfileSelect.value = state.executionProfile;
  }
  const profilesApply = state.executionProfiles.length > 0 && routeUsesExecutionProfiles();
  els.modelProfileSection.hidden = !profilesApply;
  const supervisionApplies = routeSupportsGlmSupervision();
  if (!supervisionApplies) state.supervision = "none";
  els.advisorySupervision.checked = state.supervision === "glm-5.2";
  els.advisorySupervisionText.textContent = "Start and verify GLM-5.2 advisory supervision for this task.";
  els.advisorySupervisionSection.hidden = !supervisionApplies;
  els.expectationDetails.hidden = usesHumanModes()
    && regularRoutes.length === 0
    && !profilesApply
    && els.advancedModes.children.length === 0;
  if (state.modesLoaded && usesHumanModes() && !selectedRoute() && !els.expectationDetails.hidden) {
    els.expectationDetails.open = true;
  }
  renderExpectationSummary();
}

function configureModesPayload(payload) {
  state.modeKind = payload?.kind === "managed_route" ? "managed_route" : "strategy";
  const suppliedModes = normalizeOptions(payload?.routes || payload?.modes);
  if (state.modeKind === "managed_route") {
    state.routes = suppliedModes;
    state.efforts = normalizeOptions(payload?.efforts);
    state.routeDefault = payload?.default_route || payload?.default || DEFAULT_MANAGED_MODE;
    state.effortDefault = payload?.default_effort || DEFAULT_EXECUTION_EFFORT;
  } else {
    state.routes = normalizeOptions(payload?.routes);
    state.efforts = normalizeOptions(payload?.efforts).length
      ? normalizeOptions(payload.efforts)
      : suppliedModes;
    state.routeDefault = payload?.default_route || "";
    state.effortDefault = payload?.default_effort || payload?.default || DEFAULT_PUBLIC_STRATEGY;
  }
  state.executionProfiles = normalizeOptions(payload?.execution_profiles || payload?.model_profiles);
  state.executionProfileDefault = payload?.default_execution_profile || payload?.default_model_profile || "";
  if (!state.executionProfiles.length) state.executionProfile = AUTOMATIC_PROFILE_KEY;
  state.modes = suppliedModes;
  state.modeDefault = state.modeKind === "managed_route" ? state.routeDefault : state.effortDefault;
  state.modesLoaded = true;
  renderModeControls();
  updateStartButton();
}

function updateAccessSummary() {
  const count = linesFrom(els.safeAreas).length;
  els.accessSummary.textContent = count
    ? `Work area · ${count} selected ${count === 1 ? "folder" : "folders"}`
    : "Work area · Entire project";
}

function formSnapshot() {
  return {
    objective: els.objective.value,
    safeAreas: els.safeAreas.value,
    checks: els.checks.value,
    mode: state.mode,
    route: state.route,
    effort: state.effort,
    executionProfile: state.executionProfile,
    supervision: state.supervision,
    candidateCount: els.candidateCount.value || "1",
    draftVersion: 6,
  };
}

function applyFormSnapshot(snapshot) {
  els.objective.value = snapshot.objective || "";
  els.safeAreas.value = snapshot.safeAreas || "";
  els.checks.value = snapshot.checks || "";
  state.mode = snapshot.mode || state.modeDefault;
  const legacyManagedMode = Number(snapshot.draftVersion || 0) < 4
    && ["local", "guided", "cloud", "experimental"].includes(snapshot.mode);
  if (snapshot.route) {
    state.route = snapshot.route;
  } else if (legacyManagedMode) {
    // Only the former local route has a truthful one-to-one successor. The
    // other old labels combined effort and backend routing, so require an
    // explicit route choice instead of silently changing local/cloud use.
    state.route = snapshot.mode === "local" ? "mode1" : "";
  } else {
    state.route = snapshot.mode || state.route;
  }
  const migratedEffort = {
    local: "quick",
    guided: "standard",
    cloud: "thorough",
    experimental: "quick",
  }[snapshot.mode];
  state.effort = snapshot.effort || (legacyManagedMode ? migratedEffort : snapshot.mode) || state.effort;
  state.executionProfile = snapshot.executionProfile || AUTOMATIC_PROFILE_KEY;
  state.supervision = snapshot.supervision === "glm-5.2" ? "glm-5.2" : "none";
  els.candidateCount.value = snapshot.candidateCount === "3" ? "3" : "1";
  renderModeControls();
  updateAccessSummary();
  updateStartButton();
}

function resetNewGoalForm() {
  els.objective.value = "";
  els.safeAreas.value = "";
  if (usesHumanModes()) els.checks.value = "";
  state.route = usesHumanModes()
    ? managedRouteSelection(state.routes, "", state.routeDefault)
    : state.routeDefault;
  state.effort = state.effortDefault;
  state.executionProfile = AUTOMATIC_PROFILE_KEY;
  state.supervision = "none";
  els.candidateCount.value = "1";
  state.mode = usesHumanModes() ? state.route : state.effort;
  renderModeControls();
  updateAccessSummary();
  sessionStorage.removeItem(STORAGE_KEY);
  state.restoredDraftVersion = 6;
  state.undoStack = [];
  state.redoStack = [];
  pushUndo();
  updateStartButton();
}

function reconcileCompletedDraft(task, receipt) {
  if (state.formReconciled) return;
  state.formReconciled = true;
  if (!receipt.terminal) return;
  const draft = els.objective.value.trim();
  const completedObjective = String(task.objective || "").trim();
  const legacyDraft = state.restoredDraftVersion > 0 && state.restoredDraftVersion < 2;
  if (draft && (legacyDraft || (completedObjective && draft === completedObjective))) {
    resetNewGoalForm();
  }
}

function usesHumanModes() {
  return state.modeKind === "managed_route"
    || (state.setup?.editable === false && state.setup?.worker?.type === "local_goal");
}

function pushUndo() {
  const snapshot = JSON.stringify(formSnapshot());
  if (state.undoStack.at(-1) !== snapshot) state.undoStack.push(snapshot);
  state.undoStack = state.undoStack.slice(-50);
  state.redoStack = [];
}

function undoForm() {
  if (state.undoStack.length < 2) return;
  state.redoStack.push(state.undoStack.pop());
  applyFormSnapshot(JSON.parse(state.undoStack.at(-1)));
  persistForm();
}

function redoForm() {
  const snapshot = state.redoStack.pop();
  if (!snapshot) return;
  state.undoStack.push(snapshot);
  applyFormSnapshot(JSON.parse(snapshot));
  persistForm();
}

function persistForm() {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(formSnapshot()));
}

function restoreForm() {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      const snapshot = JSON.parse(raw);
      state.restoredDraftVersion = Number(snapshot.draftVersion) || 1;
      applyFormSnapshot(snapshot);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }
  pushUndo();
}

function setBusy(busy) {
  state.busy = busy;
  updateStartButton();
  [els.startButton, els.checkButton, els.continueButton, els.acceptButton, els.stopButton, els.messageButton, els.demoButton, els.setupDemoButton].forEach((button) => {
    button.setAttribute("aria-busy", String(busy));
  });
  [els.checkButton, els.continueButton, els.acceptButton, els.stopButton, els.messageButton, els.setupDemoButton].forEach((button) => {
    button.disabled = busy;
  });
  updateDemoCallout(state.currentTask);
}

function updateStartButton() {
  const canStart = state.readiness.can_start === true;
  const hasObjective = Boolean(els.objective.value.trim());
  const hasVerification = Boolean(els.checks.value.trim());
  const verificationRequired = !usesHumanModes();
  const executionOption = selectedExecutionOption();
  const requiresScope = executionOption?.requires_scope === true
    || executionOption?.requires_enforced_scope === true;
  const experimentNeedsModel = requiresScope
    && !usesHumanModes()
    && state.setup?.worker?.type !== "model_agent";
  const experimentNeedsScope = requiresScope && !els.safeAreas.value.trim();
  const routeUnavailable = Boolean(executionOption) && !optionIsAvailable(executionOption);
  const managedRouteMissing = usesHumanModes()
    && (!state.modesLoaded || !selectedRoute());
  const managedEffortMissing = usesHumanModes()
    && (!state.modesLoaded || !selectedEffort());
  const modesLoadFailed = Boolean(state.modesError);
  const executionChoicesPending = !state.modesLoaded;
  els.startButton.disabled = state.busy || !canStart || !hasObjective
    || (verificationRequired && !hasVerification)
    || experimentNeedsModel
    || experimentNeedsScope
    || routeUnavailable
    || executionChoicesPending
    || managedRouteMissing
    || managedEffortMissing;
  if (state.busy) {
    els.startHelp.textContent = "Sending the task. A guarded local-model change can take several minutes; this page will reconnect if your phone sleeps.";
  } else if (!canStart) {
    els.startHelp.textContent = state.readiness.next_action
      || state.readiness.summary
      || "Waiting for the current task state to become ready.";
  } else if (modesLoadFailed) {
    els.startHelp.textContent = `${state.modesError} Choose Refresh to check the execution routes again.`;
  } else if (executionChoicesPending) {
    els.startHelp.textContent = "Loading the available execution choices. Start will become available after they are confirmed.";
  } else if (managedRouteMissing) {
    els.startHelp.textContent = "Choose an available execution route before starting. No cloud route will be selected automatically.";
  } else if (managedEffortMissing) {
    els.startHelp.textContent = "Loading the managed effort choices. Start will become available after one is confirmed.";
  } else if (!hasObjective) {
    els.startHelp.textContent = "Describe the outcome you want before starting.";
  } else if (verificationRequired && !hasVerification) {
    els.startHelp.textContent = "No automatic project check was found. Add one in Settings, then return here.";
  } else if (routeUnavailable) {
    els.startHelp.textContent = executionOption.disabled_reason || "The selected execution route is not available on this installation.";
  } else if (experimentNeedsModel) {
    els.startHelp.textContent = "Experiment requires a local or cloud AI connection in Settings so its file limit can be enforced.";
  } else if (experimentNeedsScope) {
    els.startHelp.textContent = "Open Access and select at least one file or folder for this bounded route.";
  } else if (!hasVerification) {
    els.startHelp.textContent = "Ready. The assistant will choose checks and show the evidence before calling this done.";
  } else {
    els.startHelp.textContent = "Ready to start this verified task.";
  }
}

function renderRecovery() {
  const readinessState = String(state.readiness?.state || "");
  const task = state.currentTask || state.liveTask || {};
  const canContinue = hasAction(task, "continue");
  const canStop = hasAction(task, "stop");
  const hasTask = Boolean(task?.id);
  const show = state.readiness?.can_start === false
    && ["needs_review", "needs_attention", "blocked", "configuration_error"].includes(readinessState);

  els.recoveryCard.hidden = !show;
  if (!show) {
    els.recoveryContinueButton.hidden = true;
    els.recoveryStopButton.hidden = true;
    els.recoveryOpenTaskButton.hidden = true;
    els.recoveryStatus.textContent = "";
    return;
  }

  const guide = task.guide && typeof task.guide === "object" ? task.guide : {};
  els.recoveryTitle.textContent = readinessState === "needs_review"
    ? guide.title || "Your result is ready"
    : readinessState === "configuration_error"
      ? "Configuration needs repair"
      : "Current task needs attention";
  els.recoverySummary.textContent = readinessState === "needs_review"
    ? guide.explanation || "The assistant finished and stopped safely for your review."
    : state.readiness?.summary
    || state.readiness?.next_action
    || "Choose what should happen before starting another task.";
  els.recoveryContinueButton.textContent = readinessState === "needs_review"
    ? "Ask for changes"
    : "Continue task";
  els.recoveryStopButton.textContent = readinessState === "needs_review"
    ? "Stop without approving"
    : "Stop task";
  els.recoveryOpenTaskButton.textContent = readinessState === "needs_review"
    ? "Review result"
    : "Open current task";
  els.recoveryContinueButton.hidden = !hasTask || !canContinue;
  els.recoveryStopButton.hidden = !hasTask || !canStop;
  els.recoveryOpenTaskButton.hidden = !hasTask;
  els.recoveryStatus.textContent = hasTask
    ? canContinue || canStop
      ? "Use a button here, or open the task to review its details and result."
      : "Open the current task to see the exact blocker and available decision."
    : "Refresh once. If this remains blocked, the installation owner must repair the configuration.";
}

function openCurrentTaskFromRecovery() {
  showView("tasks", { focus: true });
}

function continueFromRecovery() {
  const task = state.currentTask || state.liveTask || {};
  if (!task.id || !hasAction(task, "continue")) return;
  els.continueDialog.showModal();
}

function stopFromRecovery() {
  const task = state.currentTask || state.liveTask || {};
  if (!task.id || !hasAction(task, "stop")) return;
  if (window.confirm("Stop this task now? Its progress and evidence will be kept.")) {
    postAction("/api/tasks/current/stop");
  }
}

function renderHealth(health) {
  const previousSignature = state.readinessSignature;
  state.readiness = health.readiness || {};
  state.readinessSignature = `${state.readiness.state || ""}:${String(state.readiness.can_start)}:${String(state.readiness.can_queue)}`;
  const presentation = readinessPresentation();
  els.healthText.textContent = presentation.label;
  els.health.className = presentation.ready
    ? "health ok"
    : presentation.needsSetup || presentation.blocked ? "health blocked" : "health";
  els.healthIcon.setAttribute(
    "href",
    iconHref(presentation.ready
      ? "shield-check"
      : presentation.needsSetup || presentation.blocked ? "octagon-alert" : "loader-circle"),
  );
  els.health.setAttribute("aria-label", presentation.label);
  els.health.title = state.readiness.summary || presentation.label;
  renderRecovery();
  renderExpectationSummary();
  updateStartButton();
  const readinessChanged = Boolean(previousSignature)
    && previousSignature !== state.readinessSignature;
  if (usesHumanModes() && (readinessChanged || state.modesError)) {
    refreshModes({ force: readinessChanged }).catch(() => {});
  }
}

function textList(element, rows, formatter, emptyText) {
  element.replaceChildren();
  if (!Array.isArray(rows) || rows.length === 0) {
    const item = document.createElement("li");
    item.className = "empty-item";
    item.textContent = emptyText;
    element.append(item);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("li");
    const formatted = formatter(row);
    item.textContent = formatted.text;
    if (formatted.className) item.className = formatted.className;
    element.append(item);
  });
}

function previewList(element, rows, kind, emptyText, goalId = "") {
  element.replaceChildren();
  if (!Array.isArray(rows) || rows.length === 0) {
    const item = document.createElement("li");
    item.className = "empty-item";
    item.textContent = emptyText;
    element.append(item);
    return;
  }
  rows.forEach((row) => {
    const path = typeof row === "string" ? row : row.path || "";
    const label = typeof row === "string"
      ? row
      : kind === "file"
        ? `${row.status || "changed"}: ${path}`
        : row.name || path || "Artifact";
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "evidence-link";
    button.textContent = label;
    button.disabled = !path;
    button.addEventListener("click", () => openPreview(kind, path, goalId));
    item.append(button);
    element.append(item);
  });
}

async function openPreview(kind, path, goalId = "") {
  const baseRoute = kind === "file"
    ? `/api/tasks/current/file?path=${encodeURIComponent(path)}`
    : `/api/tasks/current/artifact?path=${encodeURIComponent(path)}`;
  const route = goalId
    ? `${baseRoute}&goal_id=${encodeURIComponent(goalId)}`
    : baseRoute;
  try {
    const preview = await api(route);
    els.previewTitle.textContent = preview.path || "Evidence preview";
    els.previewContent.textContent = preview.content || "";
    els.previewDialog.showModal();
  } catch (error) {
    window.alert(error instanceof Error ? error.message : String(error));
  }
}

function normalizeProgress(task) {
  if (task.progress && typeof task.progress === "object") return task.progress;
  if (Number.isFinite(task.progress)) return { determinate: true, percent: task.progress };
  return { determinate: false, percent: null };
}

function hasAction(task, name) {
  return Array.isArray(task.allowed_actions)
    && task.allowed_actions.some((row) => row && row.action === name && row.enabled !== false);
}

function receiptContext(task) {
  const final = task.final_result && typeof task.final_result === "object"
    ? task.final_result
    : {};
  const category = task.result_category || "in_progress";
  return {
    category,
    final,
    terminal: ["verified_done", "blocked", "failed"].includes(category),
  };
}

function verificationSource(row) {
  return row.source || (row.independent ? "independent" : "worker-reported");
}

function verificationSourceLabel(row) {
  const source = verificationSource(row);
  if (source === "independent") return "Independent";
  if (source === "managed-review") return "Managed review";
  return "Worker-reported";
}

function independentReviewRows(final) {
  const rows = [];
  (Array.isArray(final.review_attempts) ? final.review_attempts : []).forEach((attempt) => {
    (Array.isArray(attempt.checks) ? attempt.checks : []).forEach((check) => {
      const source = verificationSource(check);
      if (source !== "independent") return;
      rows.push({
        attempt: attempt.number || 1,
        source,
        passed: check.passed === true,
        message: check.message || check.name || attempt.summary || "Verification recorded",
      });
    });
  });
  if (rows.length) return rows;
  (Array.isArray(final.checks) ? final.checks : []).forEach((check) => {
    if (!check || typeof check !== "object") return;
    const source = verificationSource(check);
    if (source !== "independent") return;
    rows.push({
      attempt: 1,
      source,
      passed: check.passed === true,
      message: check.message || check.name || "Verification recorded",
    });
  });
  return rows;
}

function renderFinalReceipt(task, receipt) {
  const { final, terminal, category } = receipt;
  els.completedDetails.hidden = !terminal;
  els.finalResult.hidden = !terminal;
  els.finalResult.className = `final-result ${category}`;
  els.finalLabel.textContent = terminal ? final.label || "Result" : "Result";
  els.finalReason.textContent = terminal ? final.reason || final.summary || "" : "";
  const workerClaim = final.worker_claim && typeof final.worker_claim === "object"
    ? final.worker_claim
    : {};
  els.finalWorkerClaimLabel.textContent = task.metadata?.demo?.enabled === true
    ? workerClaim.label || "Scripted worker report (not AI)"
    : "Assistant report";
  els.finalWorkerClaim.textContent = workerClaim.summary || "No assistant completion report was recorded.";
  els.finalAttempts.textContent = String(Number.isFinite(final.attempts) ? final.attempts : 0);
  els.finalRetries.textContent = String(Number.isFinite(final.retries) ? final.retries : 0);
  const changedEvidence = final.what_changed_evidence && typeof final.what_changed_evidence === "object"
    ? final.what_changed_evidence
    : task.changed_files_evidence || {};
  const noChangedFiles = changedEvidence.available === false && changedEvidence.reason
    ? changedEvidence.reason
    : "No workspace changes recorded.";
  previewList(
    els.finalChangedFiles,
    Array.isArray(final.what_changed) ? final.what_changed : task.changed_files,
    "file",
    noChangedFiles,
    task.id || "",
  );
  const verificationCommands = Array.isArray(final.verification_commands)
    ? final.verification_commands
    : [];
  const verificationRows = [
    ...verificationCommands.map((command, index) => ({ command, index })),
    ...independentReviewRows(final),
  ];
  textList(els.finalVerification, verificationRows, (row) => (
    row.command
      ? {
        text: `Command ${row.index + 1}: ${row.command}`,
        className: "command",
      }
      : {
        text: `Attempt ${row.attempt} · ${row.source} · ${row.passed ? "Passed" : "Failed"}: ${row.message}`,
        className: row.passed ? "passed" : "failed",
      }
  ), "No independent verification evidence recorded.");
  const remaining = Array.isArray(final.remaining) ? final.remaining : [];
  els.finalRemaining.textContent = remaining.length
    ? `Still open: ${remaining.join("; ")}`
    : "Nothing remains open.";
}

function renderTaskGuide(task) {
  const guide = task.guide && typeof task.guide === "object" ? task.guide : {};
  const show = Boolean(guide.title);
  els.taskGuide.hidden = !show;
  if (!show) {
    els.taskGuide.className = "task-guide";
    els.taskGuideCounts.hidden = true;
    return;
  }
  els.taskGuide.className = `task-guide ${guide.tone || "active"}`;
  els.taskGuideEyebrow.textContent = guide.eyebrow || "What is happening";
  els.taskGuideTitle.textContent = guide.title;
  els.taskGuideBody.textContent = guide.body || "";
  els.taskGuideBody.hidden = !guide.body;
  els.taskGuideExplanation.textContent = guide.explanation || "";
  els.taskGuideExplanation.hidden = !guide.explanation;
  els.taskGuideNext.textContent = guide.next_action || "";
  els.taskGuideNext.hidden = !guide.next_action;
  const counts = guide.counts && typeof guide.counts === "object" ? guide.counts : {};
  const showCounts = ["changed_files", "checks", "artifacts"]
    .some((key) => Number(counts[key] || 0) > 0);
  els.taskGuideCounts.hidden = !showCounts;
  els.taskGuideFiles.textContent = String(Number(counts.changed_files || 0));
  els.taskGuideChecks.textContent = String(Number(counts.checks || 0));
  els.taskGuideArtifacts.textContent = String(Number(counts.artifacts || 0));
}

function renderTask(task) {
  state.currentTask = task;
  els.taskContext.hidden = !state.viewingHistoryId;
  const status = task.status || "ready";
  const receipt = receiptContext(task);
  const activeStatus = ["starting", "working", "checking", "stopping", "needs_review", "needs_attention", "blocked"]
    .includes(status);
  const taskAvailabilityState = receipt.terminal ? "terminal" : activeStatus ? "active" : "idle";
  const previousTaskAvailabilityState = state.taskAvailabilityState;
  state.taskAvailabilityState = taskAvailabilityState;
  reconcileCompletedDraft(task, receipt);
  if (
    !state.viewingHistoryId
    && usesHumanModes()
    && previousTaskAvailabilityState
    && previousTaskAvailabilityState !== taskAvailabilityState
    && taskAvailabilityState !== "active"
  ) {
    refreshModes({ force: true }).catch(() => {});
  }
  const rawDoneUnverified = status === "done" && !receipt.terminal;
  const visualStatus = rawDoneUnverified
    ? "checking"
    : receipt.category === "verified_done"
    ? "done"
    : receipt.category === "failed"
      ? "stopped"
      : receipt.category === "blocked"
        ? "blocked"
        : status;
  document.body.dataset.taskActive = String(
    ["starting", "working", "checking", "stopping", "needs_review", "needs_attention", "blocked"].includes(status),
  );
  document.body.dataset.taskComplete = String(receipt.terminal);
  document.body.dataset.demo = String(task.metadata?.demo?.enabled === true);
  updateDemoCallout(task);
  const taskId = String(task.id || "");
  if (receipt.terminal && taskId !== state.lastRenderedTaskId) els.completedDetails.open = false;
  state.lastRenderedTaskId = taskId;
  const guide = task.guide && typeof task.guide === "object" ? task.guide : {};
  els.statusLabel.textContent = status === "needs_review" && guide.title
    ? guide.title
    : receipt.terminal && receipt.final.label
    ? receipt.final.label
    : rawDoneUnverified
      ? "Checking evidence"
      : task.status_label || status.replaceAll("_", " ");
  els.summary.textContent = receipt.terminal
    ? receipt.final.reason || receipt.final.summary || "No trusted result reason was recorded."
    : rawDoneUnverified
      ? "Completion is not verified yet."
      : task.summary || "No task is running.";
  renderTaskGuide(task);
  els.statusIndicator.className = `status-indicator ${visualStatus}`;
  els.statusIcon.setAttribute("href", iconHref(STATUS_ICONS[visualStatus] || "loader-circle"));
  els.statusIndicator.setAttribute("aria-label", els.statusLabel.textContent);
  els.statusIndicator.title = els.statusLabel.textContent;

  const progress = normalizeProgress(task);
  const percent = Number(progress.percent);
  const indeterminate = progress.determinate === false
    && ["starting", "working", "checking", "needs_review", "needs_attention"].includes(status);
  const determinate = progress.determinate === true && Number.isFinite(percent);
  els.progressGroup.hidden = !(determinate || indeterminate);
  if (determinate) {
    const bounded = Math.max(0, Math.min(100, percent));
    els.progressValue.textContent = `${bounded}%`;
    els.progressBar.style.width = `${bounded}%`;
    els.progressTrack.className = "progress-track";
    els.progressTrack.setAttribute("aria-valuenow", String(bounded));
    els.progressTrack.removeAttribute("aria-valuetext");
  } else if (indeterminate) {
    els.progressValue.textContent = progress.label || "In progress";
    els.progressBar.style.width = "";
    els.progressTrack.className = "progress-track indeterminate";
    els.progressTrack.removeAttribute("aria-valuenow");
    els.progressTrack.setAttribute("aria-valuetext", progress.label || "In progress");
  } else {
    els.progressTrack.className = "progress-track";
    els.progressTrack.removeAttribute("aria-valuenow");
    els.progressTrack.removeAttribute("aria-valuetext");
  }

  const current = task.current && typeof task.current === "object" ? task.current : {};
  els.currentSubgoal.textContent = task.current && task.current.current_subgoal
    ? task.current.current_subgoal
    : "Waiting for the next step";
  els.checkpoint.textContent = task.current && task.current.checkpoint
    ? task.current.checkpoint.replaceAll("_", " ")
    : "Not started";
  els.attemptsValue.textContent = String(
    Number.isFinite(receipt.final.attempts) ? receipt.final.attempts : current.cycle || 0,
  );
  const strategy = task.metadata?.strategy;
  const routeKey = task.metadata?.route_key || task.metadata?.managed_route?.key || task.metadata?.mode;
  const effortKey = task.metadata?.effort || strategy?.key || task.metadata?.strategy_key;
  const taskRoute = state.routes.find((route) => route.key === routeKey);
  const taskEffort = state.efforts.find((effort) => effort.key === effortKey);
  const routeLabel = taskRoute ? modePresentation(taskRoute).label : "";
  const effortLabel = taskEffort ? modePresentation(taskEffort).label : "";
  const tournament = task.metadata?.verified_tournament;
  const tournamentLabel = Number(tournament?.candidate_count || 0) > 1
    ? `${tournament.candidate_count} verified approaches`
    : "";
  els.workApproachValue.textContent = tournamentLabel || (routeLabel && effortLabel
    ? `${routeLabel} · ${effortLabel}`
    : routeLabel || effortLabel || (usesHumanModes() ? "Managed route" : "Configured effort"));
  const execution = task.metadata?.execution;
  if (execution?.label) {
    const location = execution.network_scope === "device"
      ? "Runs on this computer"
      : execution.network_scope === "private_network"
        ? "Uses your private network"
        : execution.data_location === "local"
      ? "Local AI"
      : execution.data_location === "cloud_and_local"
        ? "Cloud planning + local execution"
        : "Managed data route";
    els.executionSummary.textContent = `${execution.label} · ${location}`;
    els.executionSummary.title = execution.detail || execution.label;
  }

  textList(els.planList, task.plan, (row) => ({
    text: `${row.status || "pending"}: ${row.step || row.text || "Plan item"}`,
    className: String(row.status || "pending").toLowerCase(),
  }), "The plan will appear after the first model or agent response.");
  textList(els.requirementsList, task.requirements, (row) => ({
    text: `${row.status || "pending"}: ${row.text || row.id || "Requirement"}`,
    className: String(row.status || "pending").toLowerCase(),
  }), "Requirements will appear as the task is understood.");
  textList(els.eventTimeline, task.events, (row) => ({
    text: `${row.summary || "Progress recorded"}${row.checkpoint ? ` — ${row.checkpoint}` : ""}`,
    className: row.stage || "act",
  }), "No tool or check events recorded yet.");
  renderConversation(task);
  previewList(
    els.changedFiles,
    task.changed_files,
    "file",
    "No workspace changes reported yet.",
    task.id || "",
  );
  textList(els.verification, task.verification, (row) => ({
    text: typeof row === "string"
      ? row
      : `${verificationSourceLabel(row)} · ${row.passed ? "Passed" : "Needs review"}: ${row.message || row.name || "Check"}`,
    className: typeof row === "object" && row.passed ? "passed" : "failed",
  }), "No verification evidence reported yet.");
  previewList(
    els.artifacts,
    task.artifacts,
    "artifact",
    "No artifacts reported yet.",
    task.id || "",
  );

  renderFinalReceipt(task, receipt);
  const viewingHistory = Boolean(state.viewingHistoryId);
  els.currentCard.hidden = receipt.terminal;
  els.workDetailGrid.hidden = receipt.terminal;
  els.activitySection.hidden = receipt.terminal;
  els.conversationSection.hidden = receipt.terminal
    || viewingHistory
    || (!hasAction(task, "message") && !Array.isArray(task.metadata?.conversation));
  els.changedFilesEvidence.hidden = receipt.terminal;
  els.verificationEvidence.hidden = receipt.terminal;
  els.artifactsEvidence.hidden = receipt.terminal;

  els.continueButton.hidden = viewingHistory || !hasAction(task, "continue");
  els.continueButtonLabel.textContent = status === "needs_review"
    ? "Ask for changes"
    : "Continue with a note";
  els.approveSpecButton.hidden = viewingHistory || !hasAction(task, "approve_spec");
  els.approveSpecButtonLabel.textContent = task.metadata?.specification_review?.kind === "amendment"
    ? "Review changed conditions"
    : "Approve completion conditions";
  els.acceptButton.hidden = viewingHistory || !hasAction(task, "accept");
  els.acceptButtonLabel.textContent = status === "needs_review"
    ? "Approve and finish"
    : "Accept result";
  els.stopButton.hidden = viewingHistory || !hasAction(task, "stop");
  els.stopButtonLabel.textContent = status === "needs_review"
    ? "Stop without approving"
    : "Stop safely";
  els.advancedDetails.textContent = JSON.stringify({
    id: task.id || "",
    contract: task.contract || "",
    safety: task.safety || {},
    metadata: task.metadata || {},
  }, null, 2);
  renderStatusFooter(task);
  renderRecovery();
}

function renderConversation(task) {
  const conversation = Array.isArray(task.metadata?.conversation)
    ? task.metadata.conversation
    : [];
  const progress = Array.isArray(task.events) ? task.events.slice(-6) : [];
  const rows = [
    ...conversation.map((message) => ({
      role: "user",
      at: message.at || "",
      label: `You · revision ${message.revision || "?"}`,
      text: message.text || "",
      detail: message.delivery === "failed" ? "Delivery failed" : "Recorded for this run",
      revision: Number(message.revision || 0),
    })),
    ...progress.map((event, index) => ({
      role: "assistant",
      at: event.at || event.updated_at || event.timestamp || "",
      label: "OpenCode progress",
      text: `${event.summary || "Progress recorded"}${event.checkpoint ? ` — ${event.checkpoint}` : ""}`,
      detail: "Harness activity",
      revision: 100000 + index,
    })),
  ];
  rows.sort((left, right) => {
    const leftTime = Date.parse(left.at);
    const rightTime = Date.parse(right.at);
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
      return leftTime - rightTime;
    }
    return left.revision - right.revision;
  });
  els.conversationList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "conversation-empty";
    empty.textContent = "OpenCode progress and your guidance will appear here.";
    els.conversationList.append(empty);
  } else {
    rows.forEach((row) => {
      const item = document.createElement("li");
      item.className = `conversation-message ${row.role}`;
      const label = document.createElement("strong");
      label.textContent = row.label;
      const text = document.createElement("p");
      text.textContent = row.text;
      const detail = document.createElement("small");
      detail.textContent = row.detail;
      item.append(label, text, detail);
      els.conversationList.append(item);
    });
  }
  const status = String(task.status || "");
  els.messageHint.textContent = status === "needs_review"
    ? "Describe what should change. Sending this will reopen the task."
    : "Your latest guidance controls if messages conflict.";
}

function renderStatusFooter(task) {
  const current = task.current && typeof task.current === "object" ? task.current : {};
  const metadata = task.metadata && typeof task.metadata === "object" ? task.metadata : {};
  const value = current.last_event_at || metadata.updated_at;
  const observedValue = metadata.observed_at || "";
  if (!value && !observedValue) {
    els.statusUpdated.textContent = "No progress recorded yet";
    return;
  }
  const timestamp = new Date(value);
  const progressText = !value
    ? "No progress recorded yet"
    : Number.isNaN(timestamp.getTime())
    ? "Progress time unavailable"
    : `Last progress ${timestamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const observed = new Date(observedValue);
  const observedText = observedValue && !Number.isNaN(observed.getTime())
    ? `Status checked ${observed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
    : "";
  els.statusUpdated.textContent = observedText && observedValue !== value
    ? `${progressText} · ${observedText}`
    : progressText;
}

function renderHistory(tasks) {
  els.historyList.replaceChildren();
  const routeSelection = els.historyRouteFilter.value || "all";
  const knownRoutes = new Map(state.routes.map((route) => [route.key, modePresentation(route).label]));
  (tasks || []).forEach((task) => {
    const routeKey = task.metadata?.route_key || task.metadata?.managed_route?.key || task.metadata?.mode || "";
    if (routeKey && !knownRoutes.has(routeKey)) knownRoutes.set(routeKey, humanizeFact(routeKey));
  });
  els.historyRouteFilter.replaceChildren();
  const allRoutes = document.createElement("option");
  allRoutes.value = "all";
  allRoutes.textContent = "All routes";
  els.historyRouteFilter.append(allRoutes);
  knownRoutes.forEach((label, key) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = label;
    els.historyRouteFilter.append(option);
  });
  els.historyRouteFilter.value = knownRoutes.has(routeSelection) ? routeSelection : "all";

  const statusSelection = els.historyStatusFilter.value || "all";
  const filtered = (tasks || []).filter((task) => {
    const receipt = receiptContext(task);
    const category = receipt.category === "verified_done"
      ? "verified"
      : receipt.category === "failed"
        ? "failed"
        : receipt.category === "blocked" || ["blocked", "needs_attention"].includes(task.status)
          ? "blocked"
          : ["starting", "working", "checking", "needs_review", "needs_attention", "stopping"].includes(task.status)
            ? "active"
            : "all";
    const routeKey = task.metadata?.route_key || task.metadata?.managed_route?.key || task.metadata?.mode || "";
    return (statusSelection === "all" || category === statusSelection)
      && (els.historyRouteFilter.value === "all" || routeKey === els.historyRouteFilter.value);
  });
  els.historyEmpty.hidden = filtered.length > 0;
  els.historyList.hidden = filtered.length === 0;

  filtered.forEach((task) => {
    const receipt = receiptContext(task);
    const rawDoneUnverified = task.status === "done" && !receipt.terminal;
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-entry";
    const label = receipt.terminal && receipt.final.label
      ? receipt.final.label
      : rawDoneUnverified
        ? "Checking evidence"
        : task.status_label || task.status;
    const category = receipt.category === "verified_done"
      ? "verified"
      : receipt.category === "failed"
        ? "failed"
        : receipt.category === "blocked" || ["blocked", "needs_attention"].includes(task.status)
          ? "blocked"
          : "active";
    const mark = document.createElement("span");
    mark.className = `history-status-mark ${category}`;
    mark.setAttribute("aria-hidden", "true");
    const icon = document.createElementNS
      ? document.createElementNS("http://www.w3.org/2000/svg", "svg")
      : document.createElement("svg");
    icon.setAttribute("class", "icon");
    const use = document.createElementNS
      ? document.createElementNS("http://www.w3.org/2000/svg", "use")
      : document.createElement("use");
    use.setAttribute("href", iconHref(category === "verified" ? "shield-check" : category === "failed" ? "octagon-alert" : category === "blocked" ? "circle-alert" : "loader-circle"));
    icon.append(use);
    mark.append(icon);
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = task.objective || task.summary || task.id;
    const result = document.createElement("small");
    result.textContent = label;
    copy.append(title, result);
    const meta = document.createElement("span");
    meta.className = "history-entry-meta";
    const changedCount = Array.isArray(task.changed_files) ? task.changed_files.length : 0;
    const routeKey = task.metadata?.route_key || task.metadata?.managed_route?.key || task.metadata?.mode || "";
    meta.textContent = `${knownRoutes.get(routeKey) || "Configured route"} · ${changedCount} ${changedCount === 1 ? "file" : "files"}`;
    button.append(mark, copy, meta);
    button.addEventListener("click", () => {
      if (state.liveTask && state.liveTask.id === task.id) {
        state.viewingHistoryId = "";
        renderTask(state.liveTask);
        showView("tasks", { focus: true });
        return;
      }
      state.viewingHistoryId = task.id;
      renderTask(task);
      showView("tasks", { focus: true });
    });
    item.append(button);
    els.historyList.append(item);
  });
}

function renderSetupJourney(setup) {
  const readOnly = setup?.editable === false;
  const configured = setup?.configured === true;
  const connectionVerified = setup?.execution_validation?.verified === true;
  const hasError = Boolean(setup?.configuration_error);
  els.setupJourney.hidden = readOnly && !hasError;
  const activeStep = configured ? 3 : connectionVerified ? 2 : 1;
  [els.setupStepChoose, els.setupStepConnect, els.setupStepVerify].forEach((step, index) => {
    step.classList?.toggle("current", index + 1 === activeStep);
    step.classList?.toggle("done", index + 1 < activeStep || configured);
  });
  if (hasError) {
    els.setupJourneyImage.src = "/static/illustrations/setup-recovery.webp";
    els.setupJourneySummary.textContent = "The saved configuration needs repair. Nothing will be overwritten until it is safe to continue.";
  } else if (configured) {
    els.setupJourneyImage.src = "/static/illustrations/verified-archive.webp";
    els.setupJourneySummary.textContent = "This project is connected and independently checked. You can update the settings or return Home to start work.";
  } else {
    els.setupJourneyImage.src = "/static/illustrations/local-ai-connection.webp";
    els.setupJourneySummary.textContent = "Choose how the assistant should run. Agentic Harness will test the full connection before saving it.";
  }
}

function renderDetectedAgents(setup, worker) {
  const options = Array.isArray(setup.execution_options) ? setup.execution_options : [];
  const codingAgent = options.find((option) => option && option.key === "coding_agent");
  const agents = codingAgent && Array.isArray(codingAgent.agents) ? codingAgent.agents : [];
  if (!agents.length) return;

  const selected = setup.configured && worker.agent
    ? worker.agent
    : codingAgent.recommended_agent || agents.find((agent) => agent.available)?.key || "";
  els.codingAgentChoice.replaceChildren();
  agents.forEach((agent) => {
    const option = document.createElement("option");
    option.value = agent.key;
    option.disabled = agent.available !== true;
    option.selected = agent.key === selected;
    option.textContent = agent.available
      ? `${agent.label}${agent.recommended ? " (recommended)" : ""}`
      : `${agent.label} (not found)`;
    els.codingAgentChoice.append(option);
  });
  if (selected) els.codingAgentChoice.value = selected;
  if (!setup.configured && codingAgent.recommended) els.executionChoice.value = "coding_agent";
}

function updateDemoCallout(task = null) {
  const demo = state.setup?.demo;
  const managedOverlay = demo?.managed_overlay === true;
  const available = demo?.available === true
    && (managedOverlay || (
      state.setup?.editable !== false
      && state.setup?.configured !== true
    ));
  els.demoCallout.hidden = !available;
  if (!available) return;
  const isDemo = task?.metadata?.demo?.enabled === true;
  const active = isDemo && ["starting", "working", "checking", "stopping", "needs_review", "needs_attention"].includes(task.status);
  const verified = isDemo && task.result_category === "verified_done";
  if (active) {
    els.demoTitle.textContent = "Safe demo running";
    els.demoSummary.textContent = "The scripted practice worker is repairing the temporary calculator while the harness records progress and independent evidence.";
    els.demoButtonLabel.textContent = "Demo running…";
  } else if (verified) {
    els.demoTitle.textContent = "Demo complete. Connect real execution when you are ready.";
    els.demoSummary.textContent = "The harness rejected the scripted worker's first false completion, repaired the temporary calculator, and accepted the result only after an independent check passed.";
    els.demoButtonLabel.textContent = "Run demo again";
  } else {
    els.demoTitle.textContent = "See an independently checked result in about a minute";
    els.demoSummary.textContent = managedOverlay
      ? "Run the real harness on a temporary practice project with a scripted worker. It uses no AI model or API key, and it never changes the connected managed workspace or its current task."
      : "Run the real harness on a temporary practice project with a scripted worker. It uses no AI model or API key, and it never touches the project shown above.";
    els.demoButtonLabel.textContent = "Try safe demo";
  }
  els.demoButton.disabled = state.busy || active;
  els.setupDemoButton.disabled = state.busy || active;
  els.demoSetupButton.hidden = managedOverlay ? !isDemo || active : false;
  els.demoSetupButton.textContent = managedOverlay
    ? "Return to real workspace"
    : "Connect real work";
  els.setupDemoButton.textContent = verified
    ? "Run safe demo again"
    : active
      ? "Demo running…"
      : "Try safe demo instead";
}

function renderSetup(setup) {
  const previousSetup = state.setup;
  state.setup = setup;
  updateDemoCallout(state.currentTask);
  const humanModes = setup.editable === false && setup.worker?.type === "local_goal";
  const readOnly = setup.editable === false;
  const configurationError = setup.configuration_error || null;
  renderSetupJourney(setup);
  els.modeSection.hidden = false;
  els.checks.required = !humanModes;
  els.verificationDetails.className = "verification-details";
  els.verificationDetails.open = false;
  const verification = setup.verification || {};
  const hasCheck = Boolean(
    setup.verification_command || setup.suggested_check || verification.technical_command,
  );
  els.verificationSummary.textContent = hasCheck || humanModes
    ? "Completion check · Automatic"
    : "Completion check · Setup needed";
  els.verificationLabel.textContent = "How should completion be checked?";
  els.verificationHelp.textContent = humanModes
    ? "The managed reviewer checks every result. Add another check here only when this task needs one."
    : "Agentic Harness runs this independently. Change it only when this task needs a different project check.";
  els.setupButton.hidden = false;
  const workspace = setup.workspace || "";
  const workspaceName = workspace.split(/[\\/]/).filter(Boolean).at(-1) || "Current workspace";
  els.workspacePath.textContent = workspaceName.replaceAll("-", " ").replaceAll("_", " ");
  els.workspacePath.title = workspace || "Workspace path unavailable";
  const worker = setup.worker || {};
  els.managedSettings.hidden = !readOnly || Boolean(configurationError);
  els.editableSettings.hidden = readOnly;
  els.configurationError.hidden = !configurationError;
  els.configurationErrorText.textContent = configurationError?.summary || configurationError?.message || "The existing configuration is invalid.";
  els.managedSettingsSummary.textContent = setup.management?.summary
    || "These settings are controlled by this installation and are shown here for reference.";
  els.managedWorkspace.textContent = workspaceName.replaceAll("-", " ").replaceAll("_", " ");
  els.managedExecution.textContent = worker.label || setup.execution_summary || "Managed automatically";
  els.managedVerification.textContent = verification.label || "Automatic evidence checks";
  els.automaticCheckLabel.textContent = verification.label || (hasCheck ? "Automatic project check" : "Project check needed");
  els.automaticCheckDetail.textContent = hasCheck
    ? "This project check runs independently before a task can be marked verified."
    : "Agentic Harness could not identify a trustworthy project test. Add a technical check below.";
  renderDetectedAgents(setup, worker);
  const executionValidation = setup.execution_validation || {};
  const currentExecution = state.currentTask?.metadata?.execution;
  if (state.currentTask?.metadata?.demo?.enabled === true && currentExecution?.label) {
    els.executionSummary.textContent = `${currentExecution.label} · Data stays local`;
    els.executionSummary.title = currentExecution.detail || currentExecution.label;
  } else {
    els.executionSummary.textContent = setup.configured
      ? worker.type === "model_agent"
        ? `${worker.model || "Model"} · ${worker.network_scope === "device"
          ? "runs on this computer"
          : worker.network_scope === "private_network"
            ? "uses your private network"
            : "uses a cloud provider"}`
          : worker.type === "local_goal"
          ? setup.execution_summary || "Managed runtime · route shown on active task"
          : executionValidation.verified
            ? `${worker.label || "Coding app"} · connection verified`
            : `${worker.label || "AI connection"} · connection not tested`
      : configurationError ? "Settings need repair" : "Settings required";
  }
  const previousCheck = previousSetup
    ? previousSetup.verification_command || previousSetup.suggested_check || ""
    : "";
  const effectiveCheck = setup.verification_command || setup.suggested_check || "";
  els.assuranceMode.value = setup.assurance_mode || "specification_frozen";
  if (!els.verificationCommand.value.trim() || els.verificationCommand.value === previousCheck) {
    els.verificationCommand.value = effectiveCheck;
  }
  if (!els.checks.value.trim() || els.checks.value === previousCheck) {
    els.checks.value = effectiveCheck;
  }
  if (setup.provider) {
    els.providerEndpoint.value = setup.provider.endpoint || "";
    els.providerModel.value = setup.provider.model || "";
    els.providerApiKeyEnv.value = setup.provider.api_key_env || "";
    els.executionChoice.value = setup.provider.data_location === "cloud" ? "cloud_model" : "local_model";
  } else if (worker.type === "coding_agent") {
    els.executionChoice.value = "coding_agent";
  }
  renderProviderTemplates(setup);
  if (setup.limits) {
    els.maxCycles.value = String(setup.limits.max_cycles || 100);
    els.maxMinutes.value = String(
      Math.max(1, Math.round((setup.limits.max_elapsed_seconds || 7200) / 60)),
    );
    els.maxTokens.value = String(setup.limits.max_total_tokens || 500000);
    els.maxProviderCalls.value = String(setup.limits.max_provider_calls || 200);
    els.maxToolCalls.value = String(setup.limits.max_tool_calls || 1000);
  }
  updateSetupFields();
  renderModeControls();
  updateStartButton();
  if (!readOnly && setup.local_model_detection && state.localModelDetection === null) {
    refreshLocalModelDetection().catch(() => {});
  } else {
    renderLocalModelDetection(state.localModelDetection || setup.local_model_detection);
  }
  if (
    (configurationError || (
      setup.configured === false
      && setup.editable !== false
      && setup.demo?.available !== true
    ))
    && !state.setupPrompted
  ) {
    state.setupPrompted = true;
    showView("settings", { focus: true });
  }
}

function renderProviderTemplates(setup) {
  const baseTemplates = Array.isArray(setup.provider_templates)
    ? setup.provider_templates
    : [{ key: "custom", label: "Custom OpenAI-compatible provider" }];
  const detected = new Map(
    (state.localModelDetection?.detected || []).map((row) => [row.template_key, row]),
  );
  const templates = baseTemplates.map((template) => {
    const local = detected.get(template.key);
    return local
      ? {
          ...template,
          detected: true,
          endpoint: local.endpoint || template.endpoint,
          model: local.model || template.model,
        }
      : template;
  });
  state.providerTemplates = templates;
  refreshProviderPresets();
}

function refreshProviderPresets() {
  const execution = els.executionChoice.value;
  const previous = els.providerPreset.value;
  const location = execution === "local_model"
    ? "local"
    : execution === "cloud_model"
      ? "cloud"
      : "";
  const templates = state.providerTemplates.filter((template) => (
    template.key === "custom"
    || !location
    || !template.data_location
    || template.data_location === "both"
    || template.data_location === location
  ));
  els.providerPreset.replaceChildren();
  templates.forEach((template) => {
    const option = document.createElement("option");
    option.value = template.key;
    option.textContent = `${template.label}${template.detected ? " (detected)" : ""}`;
    els.providerPreset.append(option);
  });
  els.providerPreset.value = templates.some((template) => template.key === previous)
    ? previous
    : "custom";
  els.providerPresetHelp.textContent = location === "local"
    ? "Choose a local-server preset, then enter the exact model ID loaded by that server."
    : location === "cloud"
      ? "Templates only pre-fill editable values. Your provider account controls model and entitlement availability."
      : "Choose an execution method to see matching provider templates.";
}

function applyProviderTemplate() {
  const template = state.providerTemplates.find((row) => row.key === els.providerPreset.value);
  if (!template || template.key === "custom") return;
  els.providerEndpoint.value = template.endpoint || "";
  els.providerModel.value = template.model || "";
  els.providerApiKeyEnv.value = template.api_key_env || "";
  els.connectionResult.textContent = template.entitlement_note || template.description || "";
  updateSetupFields();
}

function updateSetupFields({ resetProvider = false } = {}) {
  const execution = els.executionChoice.value;
  const model = execution !== "coding_agent";
  if (resetProvider && model) {
    els.providerEndpoint.value = "";
    els.providerModel.value = "";
    els.providerApiKeyEnv.value = "";
    els.providerApiKey.value = "";
    els.confirmRemoteData.checked = false;
    els.connectionResult.textContent = "";
  }
  els.providerFields.hidden = !model;
  els.codingAgentFields.hidden = model;
  els.remoteDataRow.hidden = execution !== "cloud_model";
  els.localModelRequirement.hidden = execution !== "local_model";
  els.localModelDetectionRow.hidden = execution !== "local_model";
  renderLocalModelDetection(state.localModelDetection || state.setup?.local_model_detection);
  els.executionDisclosure.textContent = execution === "local_model"
    ? "No cloud account is required. Connect AI already running on this computer or your private network."
    : execution === "cloud_model"
      ? "The selected file excerpts and tool results may be sent to your provider. You supply and control that account."
      : "Use a coding app already installed and signed in on this computer. Agentic Harness adds the workflow and independent checking.";
  if (model) refreshProviderPresets();
}

async function refreshHealth() {
  return singleFlight("health", async () => {
    renderHealth(await api("/api/health"));
  });
}

async function refreshSetup() {
  renderSetup(await api("/api/setup"));
}

function renderLocalModelDetection(payload) {
  const result = payload && typeof payload === "object"
    ? payload
    : { status: "not_checked", detected: [], summary: "Find Ollama, LM Studio, vLLM, or llama.cpp on this computer." };
  const detected = Array.isArray(result.detected) ? result.detected : [];
  els.localModelDetection.textContent = result.status === "checking"
    ? "Looking for Ollama, LM Studio, vLLM, and llama.cpp…"
    : result.summary || "No supported local model server was detected.";
  els.detectedModelChoice.replaceChildren();
  detected.forEach((server, serverIndex) => {
    const models = Array.isArray(server.models) && server.models.length
      ? server.models
      : [server.model || ""];
    models.forEach((model, modelIndex) => {
      const option = document.createElement("option");
      option.value = `${serverIndex}:${modelIndex}`;
      option.textContent = `${server.label}${model ? ` · ${model}` : " · enter model manually"}`;
      els.detectedModelChoice.append(option);
    });
  });
  els.detectedModelChoice.hidden = detected.length === 0;
  els.useDetectedModelButton.hidden = detected.length === 0;
  els.useDetectedModelButton.textContent = "Use this AI";
  const found = detected.length > 0;
  if (state.setup?.configured !== true && els.executionChoice.value === "local_model") {
    els.setupJourneyImage.src = found
      ? "/static/illustrations/local-ai-connection.webp"
      : "/static/illustrations/setup-recovery.webp";
    els.setupStepChoose.classList?.add("done");
    els.setupStepChoose.classList?.remove("current");
    els.setupStepConnect.classList?.toggle("current", !found);
    els.setupStepConnect.classList?.toggle("done", found);
    els.setupStepVerify.classList?.toggle("current", found);
    els.setupJourneySummary.textContent = found
      ? "Local AI found. Select the model, then save to run the structured-action verification."
      : "No supported local AI is ready yet. Start a server or open Manual connection, then try again.";
  }
  els.localModelGuide.hidden = found || els.executionChoice.value !== "local_model";
  els.localModelRequirement.textContent = found
    ? "Local AI found. Use it, test the connection, and save your settings."
    : "No ready local AI was found. Follow the beginner steps below, then choose Find local AI.";
}

async function refreshLocalModelDetection() {
  return singleFlight("local-model-detection", async () => {
    state.localModelDetection = {
      status: "checking",
      detected: [],
      summary: "Looking for Ollama, LM Studio, vLLM, and llama.cpp…",
    };
    renderLocalModelDetection(state.localModelDetection);
    try {
      state.localModelDetection = await api("/api/setup/local-models");
    } catch (error) {
      state.localModelDetection = {
        status: "unavailable",
        detected: [],
        summary: error instanceof Error ? error.message : "Local model detection is unavailable.",
      };
    }
    renderLocalModelDetection(state.localModelDetection);
    if (state.setup) renderProviderTemplates(state.setup);
    updateSetupFields();
    return state.localModelDetection;
  });
}

function useDetectedLocalModel() {
  const detected = state.localModelDetection?.detected;
  if (!Array.isArray(detected) || !detected.length) return;
  const [serverValue, modelValue] = String(els.detectedModelChoice.value || "0:0").split(":");
  const serverIndex = Number(serverValue) || 0;
  const modelIndex = Number(modelValue) || 0;
  const first = detected[serverIndex] || detected[0];
  const models = Array.isArray(first.models) && first.models.length
    ? first.models
    : [first.model || ""];
  const selectedModel = models[modelIndex] || models[0] || "";
  els.executionChoice.value = "local_model";
  updateSetupFields();
  if (first.template_key) els.providerPreset.value = first.template_key;
  applyProviderTemplate();
  els.providerEndpoint.value = first.endpoint || els.providerEndpoint.value;
  els.providerModel.value = selectedModel || els.providerModel.value;
  if (!selectedModel) els.manualConnectionDetails.open = true;
  els.connectionResult.textContent = `${first.label}${selectedModel ? ` · ${selectedModel}` : ""} is selected. Choose Save and test settings.`;
}

async function startDemo() {
  await runAction(async () => {
    const task = await api("/api/demo", {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.pendingStartObjective = "";
    state.viewingHistoryId = "";
    state.liveTask = task;
    renderTask(task);
    showView("tasks", { focus: true });
    await Promise.all([refreshHistory(), refreshSetup()]);
  });
}

async function dismissDemo() {
  await runAction(async () => {
    const task = await api("/api/demo/dismiss", {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.pendingStartObjective = "";
    state.viewingHistoryId = "";
    state.liveTask = task;
    renderTask(task);
    showView("tasks", { focus: true });
    await Promise.all([refreshHistory(), refreshSetup()]);
  });
}

async function refreshModes({ force = false } = {}) {
  const now = Date.now();
  if (
    !force
    && state.lastModesRefreshAt
    && now - state.lastModesRefreshAt < MODES_REFRESH_MIN_INTERVAL_MS
  ) {
    return null;
  }
  return singleFlight("modes", async () => {
    state.lastModesRefreshAt = Date.now();
    try {
      const payload = await api("/api/modes");
      const fallback = payload.kind === "managed_route"
        ? DEFAULT_MANAGED_MODE
        : DEFAULT_PUBLIC_STRATEGY;
      state.modesError = "";
      configureModesPayload({ ...payload, default: payload.default || fallback });
      return payload;
    } catch (error) {
      state.modesLoaded = false;
      state.modesError = error instanceof Error
        ? error.message
        : "The execution routes could not be loaded.";
      renderExpectationSummary();
      updateStartButton();
      throw error;
    }
  });
}

async function refreshTaskAndModes() {
  await Promise.all([
    refreshTask(true),
    refreshModes({ force: true }),
  ]);
  connectStatusStream();
}

function taskMatchesPendingStart(task) {
  if (!state.pendingStartObjective) return true;
  return String(task?.objective || "").trim() === state.pendingStartObjective;
}

function adoptLiveTask(task, { force = false } = {}) {
  if (!taskMatchesPendingStart(task)) return false;
  state.liveTask = task;
  state.pendingStartObjective = "";
  if (force || !state.viewingHistoryId) {
    state.viewingHistoryId = "";
    renderTask(task);
  }
  return true;
}

async function refreshTask(force = false) {
  return singleFlight("task", async () => {
    const task = await api("/api/tasks/current");
    adoptLiveTask(task, { force });
  });
}

async function refreshHistory() {
  return singleFlight("history", async () => {
    const query = encodeURIComponent(els.historySearch.value.trim());
    const payload = await api(`/api/tasks/history${query ? `?q=${query}` : ""}`);
    renderHistory(payload.tasks || []);
  });
}

function singleFlight(key, operation) {
  if (state.refreshes[key]) return state.refreshes[key];
  const pending = Promise.resolve().then(operation);
  const tracked = pending.finally(() => {
    if (state.refreshes[key] === tracked) delete state.refreshes[key];
  });
  state.refreshes[key] = tracked;
  return tracked;
}

async function startWork() {
  if (els.startButton.disabled) return;
  const objective = els.objective.value.trim();
  await runAction(async () => {
    const submittedAt = new Date().toISOString();
    state.pendingStartObjective = objective;
    const pendingTask = {
      id: `pending-${Date.now()}`,
      objective,
      human_title: objective.slice(0, 80),
      status: "starting",
      status_label: "Starting",
      result_category: "in_progress",
      summary: "Your task was sent. The assistant is preparing it; you can safely return to this page if the connection changes.",
      progress: { determinate: false, percent: null, label: "Starting" },
      current: {
        cycle: 0,
        current_subgoal: "Preparing the task",
        checkpoint: "Connecting",
        last_event_at: submittedAt,
      },
      plan: [
        { status: "in_progress", step: "Understand the request" },
        { status: "pending", step: "Complete the requested work" },
        { status: "pending", step: "Verify the result" },
      ],
      requirements: [{ status: "active", text: `Requested outcome: ${objective}` }],
      events: [{ stage: "act", summary: "Task sent to the assistant", checkpoint: "Starting" }],
      allowed_actions: [],
      metadata: {
        updated_at: submittedAt,
        route_key: usesHumanModes() ? state.route : undefined,
        route_id: usesHumanModes() ? selectedRoute()?.route_id : undefined,
        effort: state.effort,
        execution_profile: routeUsesExecutionProfiles()
          ? state.executionProfile
          : undefined,
        supervision: routeSupportsGlmSupervision() ? state.supervision : undefined,
      },
    };
    state.liveTask = pendingTask;
    renderTask(pendingTask);
    showView("tasks", { focus: true });
    let task;
    try {
      task = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          route: usesHumanModes() ? state.route : undefined,
          route_id: usesHumanModes() ? selectedRoute()?.route_id : undefined,
          effort: usesHumanModes() ? state.effort : undefined,
          execution_profile: usesHumanModes()
            && routeUsesExecutionProfiles()
            && state.executionProfile !== AUTOMATIC_PROFILE_KEY
            ? state.executionProfile
            : undefined,
          supervision: usesHumanModes() && routeSupportsGlmSupervision()
            ? state.supervision
            : undefined,
          strategy: usesHumanModes() ? undefined : state.effort,
          objective,
          safe_areas: linesFrom(els.safeAreas),
          checks: linesFrom(els.checks),
          candidate_count: usesHumanModes() ? 1 : Number(els.candidateCount.value || 1),
        }),
      }, true, START_TIMEOUT_MS);
    } catch (startError) {
      let recovered;
      try {
        recovered = await api("/api/tasks/current");
      } catch {
        state.pendingStartObjective = "";
        throw startError;
      }
      if (
        !["starting", "working", "checking", "needs_review", "needs_attention", "blocked"].includes(recovered.status)
        || !taskMatchesPendingStart(recovered)
      ) {
        state.pendingStartObjective = "";
        throw startError;
      }
      recovered.summary = "Your task was accepted and is running. This page reconnected to the current task.";
      task = recovered;
    }
    if (task.metadata?.start_accepted === false) {
      state.pendingStartObjective = "";
      adoptLiveTask(task, { force: true });
      await refreshHistory();
      return;
    }
    if (!taskMatchesPendingStart(task)) {
      const recovered = await api("/api/tasks/current");
      if (taskMatchesPendingStart(recovered)) task = recovered;
    }
    if (!taskMatchesPendingStart(task)) {
      state.pendingStartObjective = "";
      throw new Error("The new task was not confirmed. Your draft is still here; review the current task and try again.");
    }
    adoptLiveTask(task, { force: true });
    resetNewGoalForm();
    await refreshHistory();
  });
}

async function postAction(path, body = {}) {
  await runAction(async () => {
    const task = await api(path, { method: "POST", body: JSON.stringify(body) });
    state.viewingHistoryId = "";
    state.liveTask = task;
    renderTask(task);
    showView("tasks", { focus: true });
    await refreshHistory();
  });
}

async function saveSetup(event) {
  event.preventDefault();
  if (state.setupBusy) return;
  state.setupBusy = true;
  els.saveSetupButton.disabled = true;
  els.saveSetupButton.setAttribute("aria-busy", "true");
  const saveLabel = els.saveSetupButton.textContent;
  els.saveSetupButton.textContent = "Testing settings…";
  els.setupError.textContent = "";
  const apiKey = els.providerApiKey.value.trim();
  const payload = {
    execution: els.executionChoice.value,
    agent: els.codingAgentChoice.value,
    endpoint: els.providerEndpoint.value.trim(),
    model: els.providerModel.value.trim(),
    api_key_env: apiKey ? "" : els.providerApiKeyEnv.value.trim(),
    api_key: apiKey,
    confirm_remote_data: els.confirmRemoteData.checked,
    verification_command: els.verificationCommand.value.trim(),
    assurance_mode: els.assuranceMode.value,
    max_cycles: Number(els.maxCycles.value),
    max_elapsed_seconds: Number(els.maxMinutes.value) * 60,
    max_total_tokens: Number(els.maxTokens.value),
    max_provider_calls: Number(els.maxProviderCalls.value),
    max_tool_calls: Number(els.maxToolCalls.value),
  };
  const recoveringSessionCredential = Boolean(
    apiKey
    && state.readiness.state === "credential_required"
    && state.setup?.worker?.type === "model_agent"
    && state.setup?.credential?.source === "session"
    && payload.endpoint === state.setup?.provider?.endpoint
    && payload.model === state.setup?.provider?.model
    && !payload.api_key_env,
  );
  try {
    if (payload.execution === "coding_agent") {
      els.codingAgentConnectionResult.textContent = "Testing…";
      const tested = await api("/api/setup/test", {
        method: "POST",
        body: JSON.stringify({ execution: "coding_agent", agent: payload.agent }),
      }, true, START_TIMEOUT_MS);
      els.codingAgentConnectionResult.textContent = tested.summary || "Coding app connection checked.";
    } else {
      els.connectionResult.textContent = "Testing…";
      const tested = await api("/api/setup/test", {
        method: "POST",
        body: JSON.stringify({
          execution: payload.execution,
          endpoint: payload.endpoint,
          model: payload.model,
          api_key_env: payload.api_key_env,
          api_key: payload.api_key,
        }),
      }, true, START_TIMEOUT_MS);
      if (tested.structured_actions !== true) {
        throw new Error("The AI connected but did not pass the structured-action test.");
      }
      els.connectionResult.textContent = "AI connection verified.";
    }
    if (recoveringSessionCredential) {
      await api("/api/setup/credential", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      });
      els.providerApiKey.value = "";
      await Promise.all([refreshSetup(), refreshHealth(), refreshTask(true)]);
      showView("tasks", { focus: true });
      return;
    }
    const configured = await api("/api/setup", { method: "POST", body: JSON.stringify(payload) });
    if (apiKey && configured.credential && configured.credential.source === "session") {
      await api("/api/setup/credential", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey }),
      });
    }
    els.providerApiKey.value = "";
    await Promise.all([refreshSetup(), refreshHealth(), refreshTask(true)]);
    showView("home", { focus: true });
  } catch (error) {
    els.providerApiKey.value = "";
    els.setupError.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    state.setupBusy = false;
    els.saveSetupButton.disabled = false;
    els.saveSetupButton.removeAttribute("aria-busy");
    els.saveSetupButton.textContent = saveLabel;
  }
}

async function runAction(action) {
  if (state.busy) return;
  setBusy(true);
  try {
    await action();
    await refreshHealth();
  } catch (error) {
    const message = error instanceof Error ? error.message : "The request failed.";
    els.summary.textContent = message;
    els.statusLabel.textContent = "Needs attention";
    if (state.activeView === "home") {
      els.startHelp.textContent = message || state.readiness?.next_action
        || "The request needs attention before this task can start.";
    }
  } finally {
    setBusy(false);
  }
}

function schedulePolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(() => {
    Promise.all([refreshTask(), refreshHealth(), refreshHistory()]).catch(() => {});
  }, 2000);
}

function stopPolling() {
  if (!state.pollTimer) return;
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function connectStatusStream() {
  if (state.authToken || !("WebSocket" in window)) {
    schedulePolling();
    return;
  }
  if (state.socket && Number(state.socket.readyState) < 2) return;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${window.location.host}/api/tasks/stream`);
  state.socket = socket;
  socket.addEventListener("open", () => {
    state.reconnectDelay = 1000;
    stopPolling();
  });
  socket.addEventListener("message", (event) => {
    try {
      const task = JSON.parse(event.data);
      adoptLiveTask(task);
      refreshHistory().catch(() => {});
      refreshHealth().catch(() => {});
    } catch {
      refreshTask().catch(() => {});
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket === socket) state.socket = null;
    schedulePolling();
    const delay = state.reconnectDelay;
    state.reconnectDelay = Math.min(30000, state.reconnectDelay * 2);
    window.setTimeout(connectStatusStream, delay);
  });
}

function recoverVisibleSession() {
  if (document.visibilityState === "hidden") return;
  Promise.all([refreshTask(true), refreshHealth(), refreshHistory()]).catch(() => {});
  connectStatusStream();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  const dark = theme === "dark";
  els.themeIcon.setAttribute("href", iconHref(dark ? "sun" : "moon"));
  els.themeButton.title = dark ? "Use light theme" : "Use dark theme";
}

function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

async function exportSession() {
  const session = await api("/api/session");
  await navigator.clipboard.writeText(JSON.stringify(session, null, 2));
  els.exportButtonLabel.textContent = "Copied";
  window.setTimeout(() => { els.exportButtonLabel.textContent = "Copy history"; }, 1200);
}

function handleShortcut(event) {
  if (!event.ctrlKey && !event.metaKey) return;
  if (event.key === "Enter") {
    event.preventDefault();
    startWork();
  } else if (event.key.toLowerCase() === "r") {
    event.preventDefault();
    runAction(refreshTaskAndModes);
  } else if (event.key.toLowerCase() === "k") {
    event.preventDefault();
    showView("history", { focus: true });
  } else if (event.key === "/") {
    event.preventDefault();
    els.shortcutsDialog.showModal();
  } else if (event.key.toLowerCase() === "z" && event.shiftKey) {
    event.preventDefault();
    redoForm();
  } else if (event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoForm();
  }
}

els.startButton.addEventListener("click", startWork);
els.checkButton.addEventListener("click", () => runAction(refreshTaskAndModes));
els.recoveryContinueButton.addEventListener("click", continueFromRecovery);
els.recoveryStopButton.addEventListener("click", stopFromRecovery);
els.recoveryOpenTaskButton.addEventListener("click", openCurrentTaskFromRecovery);
els.homeTab.addEventListener("click", () => showView("home"));
els.tasksTab.addEventListener("click", () => showView("tasks"));
els.historyTab.addEventListener("click", () => showView("history"));
els.setupButton.addEventListener("click", () => showView("settings"));
[els.homeTab, els.tasksTab, els.historyTab, els.setupButton].forEach((tab) => {
  tab.addEventListener("keydown", handlePrimaryTabKeydown);
});
els.demoButton.addEventListener("click", startDemo);
els.demoSetupButton.addEventListener("click", () => {
  if (state.setup?.demo?.managed_overlay === true) {
    dismissDemo();
  } else {
    showView("settings", { focus: true });
  }
});
els.setupDemoButton.addEventListener("click", startDemo);
els.closeSetupButton.addEventListener("click", () => showView("home", { focus: true }));
els.setupForm.addEventListener("submit", saveSetup);
els.executionChoice.addEventListener("change", () => updateSetupFields({ resetProvider: true }));
els.providerPreset.addEventListener("change", applyProviderTemplate);
els.useDetectedModelButton.addEventListener("click", useDetectedLocalModel);
els.checkLocalModelsButton.addEventListener("click", () => refreshLocalModelDetection());
els.continueButton.addEventListener("click", () => els.continueDialog.showModal());
els.messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = els.messageInput.value.trim();
  if (!message) {
    els.messageInput.focus();
    return;
  }
  els.messageInput.value = "";
  postAction("/api/tasks/current/message", { message });
});
els.approveSpecButton.addEventListener("click", () => {
  state.specificationReviewBinding = window.HarnessAssurance.populateDialog(state.currentTask, {
    dialog: els.specificationDialog,
    title: els.specificationTitle,
    help: els.specificationHelp,
    requirements: els.specificationRequirements,
    submit: els.approveSpecificationSubmit,
  });
});
els.closeSpecificationButton.addEventListener("click", () => els.specificationDialog.close());
els.specificationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const requirements = window.HarnessAssurance.requirementsFromText(
    els.specificationRequirements.value,
  );
  if (!requirements.length) return;
  const binding = state.specificationReviewBinding || {};
  els.specificationDialog.close();
  state.specificationReviewBinding = null;
  postAction("/api/tasks/current/approve-spec", { requirements, ...binding });
});
els.closeContinueButton.addEventListener("click", () => els.continueDialog.close());
els.continueForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const feedback = els.continueFeedback.value.trim();
  els.continueDialog.close();
  postAction("/api/tasks/current/continue", { feedback });
});
els.acceptButton.addEventListener("click", () => postAction("/api/tasks/current/accept"));
els.stopButton.addEventListener("click", () => {
  if (window.confirm("Stop after the current safe step? Progress and evidence will be kept.")) {
    postAction("/api/tasks/current/stop");
  }
});
els.returnToCurrentButton.addEventListener("click", () => {
  state.viewingHistoryId = "";
  if (state.liveTask) renderTask(state.liveTask);
  showView("tasks", { focus: true });
});
els.modeSelect.addEventListener("change", () => {
  state.effort = els.modeSelect.value || state.effortDefault;
  renderModeControls();
  pushUndo();
  persistForm();
  updateStartButton();
});
els.routeSelect.addEventListener("change", () => {
  state.route = els.routeSelect.value || state.routeDefault;
  renderModeControls();
  pushUndo();
  persistForm();
  updateStartButton();
});
els.modelProfileSelect.addEventListener("change", () => {
  state.executionProfile = els.modelProfileSelect.value || AUTOMATIC_PROFILE_KEY;
  renderModeControls();
  pushUndo();
  persistForm();
});
els.advisorySupervision.addEventListener("change", () => {
  state.supervision = els.advisorySupervision.checked ? "glm-5.2" : "none";
  renderModeControls();
  pushUndo();
  persistForm();
  updateStartButton();
});
els.candidateCount.addEventListener("change", () => {
  pushUndo();
  persistForm();
});
els.themeButton.addEventListener("click", toggleTheme);
els.shortcutsButton.addEventListener("click", () => els.shortcutsDialog.showModal());
els.historySearch.addEventListener("input", () => refreshHistory().catch(() => {}));
els.historyStatusFilter.addEventListener("change", () => refreshHistory().catch(() => {}));
els.historyRouteFilter.addEventListener("change", () => refreshHistory().catch(() => {}));
els.exportButton.addEventListener("click", () => exportSession().catch((error) => window.alert(error.message)));
(document.querySelectorAll?.("[data-task-starter]") || []).forEach((button) => {
  button.addEventListener("click", () => {
    const starters = {
      "Build or improve": "Build or improve ",
      "Fix a problem": "Find and fix ",
      "Review safely": "Review this project safely and report ",
      "Long-running task": "Complete this larger task: ",
    };
    els.objective.value = starters[button.dataset.taskStarter] || "";
    els.objective.focus();
    els.objective.setSelectionRange(els.objective.value.length, els.objective.value.length);
    pushUndo();
    persistForm();
    updateStartButton();
  });
});
[els.objective, els.safeAreas, els.checks].forEach((field) => {
  field.addEventListener("input", () => {
    if (field === els.safeAreas) updateAccessSummary();
    pushUndo();
    persistForm();
    updateStartButton();
  });
});
document.addEventListener("keydown", handleShortcut);
document.addEventListener("visibilitychange", recoverVisibleSession);
window.addEventListener("pageshow", recoverVisibleSession);
window.addEventListener("online", recoverVisibleSession);

restoreAuthToken();
applyTheme(localStorage.getItem(THEME_KEY) || "light");
showView("home");
restoreForm();
Promise.all([refreshHealth(), refreshSetup(), refreshModes({ force: true }), refreshTask(), refreshHistory()])
  .then(connectStatusStream)
  .catch((error) => {
    els.statusLabel.textContent = "Needs attention";
    els.summary.textContent = error instanceof Error ? error.message : "The app could not start cleanly.";
  });
