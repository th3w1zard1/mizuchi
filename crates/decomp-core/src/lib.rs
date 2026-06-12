mod adapter;
mod model;
mod report;
mod workspace;

pub use adapter::{registered_adapter, registered_adapters};
pub use model::{
  ActionSummary, AdapterDescriptor, AnalysisProvider, AnalysisProviderAvailability, AnalysisRecord, ArtifactComparison, ArtifactFingerprint,
  BuildArtifactSpec, BuildBackendPlan, BuildDependencyPlan, BuildInputRequirement, BuildPlan,
  BuildSystemPlan, BuildToolchainPlan, BuildUnitPlan, CaseManifest, ComparableRelocation,
  CfgComparisonReadiness, CfgEdgeEvidence, CfgEvidenceGraph, CfgFunctionEvidence,
  CommandAvailability, ComparableSection, ComparableSymbol, CompilerComponentRequirement,
  CompilerInvocationCandidate, CompilerInvocationLedger, CompilerProfile,
  DebugEvidence, DependencyExport, DependencyGraph, DependencyImport,
  BuildUnitProofResult, BuildUnitVerificationLedger, BuildUnitVerificationUnit, DependencyLinkRequirement,
  DependencyRelocationEdge, DependencyRuntimeArtifact, derive_build_unit_verification_ledger,
  derive_proof_target_ledger, ProofTargetLedger, ProofTargetUnit,
  derive_roundtrip_proof, derive_source_verification_ledger,
  FailureClass, GeneratedBuildArtifact, GnuDebugAltLink, GnuDebugLink,
  InvocationEnvironmentRequirement, InvocationToolCandidate, LinkInputPlan, LinkPlan, LinkUnit,
  MatchScore, MatchScoreComponent, ProjectSnapshot,
  ProjectStatus, ProjectStructure, ReconstructionGraph, ReportFormat, RunRequest,
  RuntimeArtifactPlan, SourceArtifactAudit, SourceAuditRecord, SourceCandidate,
  SourceVerificationArtifact, SourceVerificationLedger, StatusAttempt, StatusNextAction,
  TargetExport,
  TargetFunction, TargetImport, TargetInput,
  TargetPlatformFingerprint, TargetRelocation, TargetSection, TargetSegment, TargetSymbol,
  ToolchainEvidence, ToolchainStagePlan, TranslationUnit, TypeCandidate, TypeRelationGraph,
  TypeRelationship, TypeSymbolNode, UncertaintyItem,
  UncertaintyLedger, VerificationCheck, VerificationRecord,
};
pub use workspace::{compare_artifacts, load_project, render_report, run_request};
