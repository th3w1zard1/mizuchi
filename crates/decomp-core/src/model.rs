use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};

use anyhow::Result;
use object::{
  read::archive::ArchiveFile,
  Architecture, File, Object, ObjectSection, ObjectSegment, ObjectSymbol, RelocationTarget,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunRequest {
  pub target: PathBuf,
  pub project: PathBuf,
  pub rebuild: bool,
  pub verify: bool,
  pub match_requested: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionSummary {
  pub rebuild: bool,
  pub verify: bool,
  #[serde(rename = "match")]
  pub match_requested: bool,
}

impl ActionSummary {
  pub fn from_request(request: &RunRequest) -> Self {
    Self {
      rebuild: request.rebuild,
      verify: request.verify,
      match_requested: request.match_requested,
    }
  }
}

#[derive(Debug, Clone)]
pub struct TargetInput {
  pub path: PathBuf,
  pub bytes: Vec<u8>,
}

impl TargetInput {
  pub fn new(path: PathBuf, bytes: Vec<u8>) -> Self {
    Self { path, bytes }
  }

  pub fn file_name(&self) -> String {
    self
      .path
      .file_name()
      .and_then(|name| name.to_str())
      .unwrap_or("unknown")
      .to_string()
  }

  pub fn case_id(&self) -> String {
    let stem = self
      .path
      .file_stem()
      .and_then(|name| name.to_str())
      .unwrap_or("target");
    sanitize_identifier(stem)
  }

  pub fn sha256(&self) -> String {
    let digest = Sha256::digest(&self.bytes);
    hex::encode(digest)
  }

  pub fn file_kind_label(&self) -> String {
    match object::FileKind::parse(self.bytes.as_slice()) {
      Ok(kind) => format!("{kind:?}"),
      Err(_) => "Unknown".to_string(),
    }
  }

  pub fn parsed_architecture(&self) -> Option<Architecture> {
    File::parse(self.bytes.as_slice()).ok().map(|file| file.architecture())
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdapterDescriptor {
  pub id: String,
  #[serde(rename = "capabilitiesProfile")]
  pub capabilities_profile: String,
  #[serde(rename = "sourceType")]
  pub source_type: String,
  pub family: String,
  pub platform: String,
  #[serde(rename = "loadTool")]
  pub load_tool: String,
  #[serde(rename = "analysisProviders")]
  pub analysis_providers: Vec<AnalysisProvider>,
  #[serde(rename = "contextPath")]
  pub context_path: String,
  #[serde(rename = "supportsRecovery")]
  pub supports_recovery: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AnalysisProvider {
  pub id: String,
  pub role: String,
  pub detail: String,
}

impl AnalysisProvider {
  pub fn new(id: &str, role: &str, detail: &str) -> Self {
    Self {
      id: id.to_string(),
      role: role.to_string(),
      detail: detail.to_string(),
    }
  }
}

impl AdapterDescriptor {
  #[allow(clippy::too_many_arguments)]
  pub fn new(
    id: &str,
    capabilities_profile: &str,
    source_type: &str,
    family: &str,
    platform: &str,
    load_tool: &str,
    analysis_providers: &[AnalysisProvider],
    context_path: &str,
    supports_recovery: bool,
  ) -> Self {
    Self {
      id: id.to_string(),
      capabilities_profile: capabilities_profile.to_string(),
      source_type: source_type.to_string(),
      family: family.to_string(),
      platform: platform.to_string(),
      load_tool: load_tool.to_string(),
      analysis_providers: analysis_providers.to_vec(),
      context_path: context_path.to_string(),
      supports_recovery,
    }
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseManifest {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub adapter: CaseAdapter,
  pub ingest: CaseIngest,
  pub target: CaseTarget,
  pub load: CaseLoad,
  pub symbol: CaseSymbol,
  pub proof: CaseProof,
  pub workspace: CaseWorkspace,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseAdapter {
  pub id: String,
  #[serde(rename = "capabilitiesProfile")]
  pub capabilities_profile: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseIngest {
  #[serde(rename = "sourceType")]
  pub source_type: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  pub provenance: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseTarget {
  pub family: String,
  pub binary: String,
  pub platform: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseLoad {
  pub tool: String,
  #[serde(default, rename = "analysisProviders")]
  pub analysis_providers: Vec<AnalysisProvider>,
  #[serde(rename = "programPath")]
  pub program_path: String,
  #[serde(rename = "contextPath")]
  pub context_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseSymbol {
  pub name: String,
  pub locator: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseProof {
  #[serde(rename = "targetObjectPath")]
  pub target_object_path: String,
  pub source: String,
  pub comparator: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaseWorkspace {
  #[serde(rename = "promptPath")]
  pub prompt_path: String,
  #[serde(rename = "buildDir")]
  pub build_dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolAvailability {
  pub objdiff: bool,
  pub ghidra_headless: bool,
  #[serde(rename = "compilePlaceholder")]
  pub compile_placeholder: bool,
  #[serde(rename = "analysisProviders")]
  pub analysis_providers: Vec<AnalysisProviderAvailability>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnalysisProviderAvailability {
  pub id: String,
  pub role: String,
  pub kind: String,
  pub available: bool,
  pub detail: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnalysisRecord {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub adapter: AdapterDescriptor,
  pub target: AnalysisTarget,
  #[serde(rename = "toolAvailability")]
  pub tool_availability: ToolAvailability,
  pub notes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnalysisTarget {
  pub path: String,
  #[serde(rename = "fileName")]
  pub file_name: String,
  #[serde(rename = "fileKind")]
  pub file_kind: String,
  #[serde(rename = "platformFingerprint")]
  pub platform_fingerprint: TargetPlatformFingerprint,
  #[serde(rename = "sizeBytes")]
  pub size_bytes: u64,
  pub sha256: String,
  pub architecture: String,
  pub endianness: String,
  #[serde(rename = "entryPoint")]
  pub entry_point: u64,
  #[serde(rename = "buildId")]
  pub build_id: Option<String>,
  pub sections: Vec<TargetSection>,
  pub segments: Vec<TargetSegment>,
  pub symbols: Vec<TargetSymbol>,
  #[serde(rename = "dynamicSymbols")]
  pub dynamic_symbols: Vec<TargetSymbol>,
  pub functions: Vec<TargetFunction>,
  pub relocations: Vec<TargetRelocation>,
  pub imports: Vec<TargetImport>,
  pub exports: Vec<TargetExport>,
  #[serde(rename = "archiveKind")]
  pub archive_kind: Option<String>,
  #[serde(rename = "archiveIsThin")]
  pub archive_is_thin: Option<bool>,
  #[serde(rename = "archiveMembers")]
  pub archive_members: Vec<TargetArchiveMember>,
  pub debug: DebugEvidence,
  pub toolchain: ToolchainEvidence,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetPlatformFingerprint {
  #[serde(rename = "objectFormat")]
  pub object_format: String,
  #[serde(rename = "pointerWidthBits")]
  pub pointer_width_bits: Option<u8>,
  pub vendor: String,
  #[serde(rename = "operatingSystem")]
  pub operating_system: String,
  pub environment: String,
  #[serde(rename = "binaryInterfaceHypotheses")]
  pub binary_interface_hypotheses: Vec<String>,
  #[serde(rename = "tripleCandidates")]
  pub triple_candidates: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetSection {
  pub name: String,
  pub size: u64,
  #[serde(rename = "address")]
  pub address: u64,
  pub kind: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetSegment {
  pub name: String,
  pub address: u64,
  pub size: u64,
  pub align: u64,
  #[serde(rename = "fileOffset")]
  pub file_offset: u64,
  #[serde(rename = "fileSize")]
  pub file_size: u64,
  pub flags: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetSymbol {
  pub name: String,
  pub address: u64,
  pub size: u64,
  pub kind: String,
  pub scope: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetFunction {
  pub name: String,
  pub address: u64,
  pub size: u64,
  pub source: String,
  pub confidence: String,
  #[serde(rename = "cfgStatus")]
  pub cfg_status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeRelationGraph {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "symbolCount")]
  pub symbol_count: usize,
  #[serde(rename = "typeCandidateCount")]
  pub type_candidate_count: usize,
  #[serde(rename = "relationshipCount")]
  pub relationship_count: usize,
  #[serde(rename = "unresolvedTypeCount")]
  pub unresolved_type_count: usize,
  pub symbols: Vec<TypeSymbolNode>,
  #[serde(rename = "typeCandidates")]
  pub type_candidates: Vec<TypeCandidate>,
  pub relationships: Vec<TypeRelationship>,
  pub uncertainty: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CfgEvidenceGraph {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "functionCount")]
  pub function_count: usize,
  #[serde(rename = "recoveredFunctionCount")]
  pub recovered_function_count: usize,
  #[serde(rename = "unresolvedFunctionCount")]
  pub unresolved_function_count: usize,
  #[serde(rename = "edgeCount")]
  pub edge_count: usize,
  pub functions: Vec<CfgFunctionEvidence>,
  pub edges: Vec<CfgEdgeEvidence>,
  #[serde(rename = "comparisonReadiness")]
  pub comparison_readiness: CfgComparisonReadiness,
  pub uncertainty: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CfgFunctionEvidence {
  pub id: String,
  pub name: String,
  pub status: String,
  pub confidence: String,
  #[serde(rename = "boundarySource")]
  pub boundary_source: String,
  #[serde(rename = "basicBlockCount")]
  pub basic_block_count: Option<usize>,
  #[serde(rename = "edgeCount")]
  pub edge_count: Option<usize>,
  pub evidence: Vec<String>,
  #[serde(rename = "missingEvidence")]
  pub missing_evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CfgEdgeEvidence {
  pub id: String,
  pub from: String,
  pub to: String,
  pub kind: String,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CfgComparisonReadiness {
  pub status: String,
  #[serde(rename = "targetCfgAvailable")]
  pub target_cfg_available: bool,
  #[serde(rename = "candidateCfgAvailable")]
  pub candidate_cfg_available: bool,
  #[serde(rename = "comparisonArtifact")]
  pub comparison_artifact: String,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeSymbolNode {
  pub id: String,
  pub name: String,
  pub kind: String,
  pub scope: String,
  pub address: u64,
  pub size: u64,
  #[serde(rename = "demangleStatus")]
  pub demangle_status: String,
  #[serde(rename = "demangledName")]
  pub demangled_name: Option<String>,
  pub namespace: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeCandidate {
  pub id: String,
  pub kind: String,
  pub name: String,
  pub status: String,
  pub confidence: String,
  #[serde(rename = "sourceSymbols")]
  pub source_symbols: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypeRelationship {
  pub from: String,
  pub to: String,
  pub kind: String,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetRelocation {
  pub section: String,
  pub offset: u64,
  pub size: u8,
  pub kind: String,
  pub encoding: String,
  pub target: String,
  pub addend: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetImport {
  pub library: String,
  pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetExport {
  pub name: String,
  pub address: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetArchiveMember {
  pub id: String,
  pub name: String,
  #[serde(rename = "fileKind")]
  pub file_kind: String,
  pub architecture: String,
  pub endianness: String,
  #[serde(rename = "sizeBytes")]
  pub size_bytes: u64,
  pub sha256: String,
  #[serde(rename = "objectFormat")]
  pub object_format: String,
  #[serde(rename = "parserStatus")]
  pub parser_status: String,
  #[serde(rename = "isThin")]
  pub is_thin: bool,
  #[serde(rename = "sectionCount")]
  pub section_count: usize,
  #[serde(rename = "symbolCount")]
  pub symbol_count: usize,
  #[serde(rename = "functionCount")]
  pub function_count: usize,
  #[serde(rename = "relocationCount")]
  pub relocation_count: usize,
  #[serde(rename = "importCount")]
  pub import_count: usize,
  #[serde(rename = "exportCount")]
  pub export_count: usize,
  pub date: Option<u64>,
  pub uid: Option<u64>,
  pub gid: Option<u64>,
  pub mode: Option<u64>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyGraph {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "importCount")]
  pub import_count: usize,
  #[serde(rename = "exportCount")]
  pub export_count: usize,
  #[serde(rename = "relocationEdgeCount")]
  pub relocation_edge_count: usize,
  #[serde(rename = "runtimeArtifactCount")]
  pub runtime_artifact_count: usize,
  #[serde(rename = "unresolvedDependencyCount")]
  pub unresolved_dependency_count: usize,
  pub imports: Vec<DependencyImport>,
  pub exports: Vec<DependencyExport>,
  #[serde(rename = "relocationEdges")]
  pub relocation_edges: Vec<DependencyRelocationEdge>,
  #[serde(rename = "runtimeArtifacts")]
  pub runtime_artifacts: Vec<DependencyRuntimeArtifact>,
  #[serde(rename = "linkRequirements")]
  pub link_requirements: Vec<DependencyLinkRequirement>,
  pub uncertainty: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyImport {
  pub id: String,
  pub library: String,
  pub symbol: String,
  pub status: String,
  pub confidence: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyExport {
  pub id: String,
  pub symbol: String,
  pub address: u64,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyRelocationEdge {
  pub id: String,
  pub section: String,
  pub offset: u64,
  pub target: String,
  pub kind: String,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyRuntimeArtifact {
  pub id: String,
  pub name: String,
  pub kind: String,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyLinkRequirement {
  pub id: String,
  pub kind: String,
  pub name: String,
  pub status: String,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DebugEvidence {
  #[serde(rename = "hasDebugSymbols")]
  pub has_debug_symbols: bool,
  #[serde(rename = "gnuDebugLink")]
  pub gnu_debuglink: Option<GnuDebugLink>,
  #[serde(rename = "gnuDebugAltLink")]
  pub gnu_debugaltlink: Option<GnuDebugAltLink>,
  #[serde(rename = "machUuid")]
  pub mach_uuid: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GnuDebugLink {
  pub file: String,
  pub crc: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GnuDebugAltLink {
  pub file: String,
  #[serde(rename = "buildId")]
  pub build_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolchainEvidence {
  #[serde(rename = "commentStrings")]
  pub comment_strings: Vec<String>,
  pub notes: Vec<String>,
  #[serde(rename = "compiler")]
  pub compiler: String,
  #[serde(rename = "linker")]
  pub linker: String,
}

impl DebugEvidence {
  fn empty() -> Self {
    Self {
      has_debug_symbols: false,
      gnu_debuglink: None,
      gnu_debugaltlink: None,
      mach_uuid: None,
    }
  }
}

impl ToolchainEvidence {
  fn unknown() -> Self {
    Self {
      comment_strings: Vec::new(),
      notes: Vec::new(),
      compiler: "unknown".to_string(),
      linker: "unknown".to_string(),
    }
  }
}

impl AnalysisRecord {
  pub fn from_target(target: &TargetInput, adapter: &AdapterDescriptor, repo_root: &Path) -> Self {
    let parsed_kind = object::FileKind::parse(target.bytes.as_slice()).ok();
    let archive_analysis = if matches!(parsed_kind, Some(object::FileKind::Archive)) {
      analyze_archive_members(target.bytes.as_slice()).ok()
    } else {
      None
    };
    let parsed = if archive_analysis.is_some() {
      None
    } else {
      File::parse(target.bytes.as_slice()).ok()
    };
    let architecture = archive_analysis
      .as_ref()
      .map(|archive| archive.architecture.clone())
      .or_else(|| parsed.as_ref().map(|file| format!("{:?}", file.architecture())))
      .unwrap_or_else(|| "Unknown".to_string());
    let pointer_width_bits = archive_analysis
      .as_ref()
      .and_then(|archive| archive.pointer_width_bits)
      .or_else(|| {
        parsed
          .as_ref()
          .and_then(|file| file.architecture().address_size())
          .map(|size| size.bytes() * 8)
      });
    let endianness = archive_analysis
      .as_ref()
      .map(|archive| archive.endianness.clone())
      .or_else(|| parsed.as_ref().map(|file| format!("{:?}", file.endianness())))
      .unwrap_or_else(|| "Unknown".to_string());
    let entry_point = parsed.as_ref().map(|file| file.entry()).unwrap_or(0);
    let build_id = archive_analysis
      .as_ref()
      .and_then(|archive| archive.build_id.clone())
      .or_else(|| {
        parsed
          .as_ref()
          .and_then(|file| file.build_id().ok().flatten())
          .map(hex::encode)
      });
    let sections = archive_analysis
      .as_ref()
      .map(|archive| archive.sections.clone())
      .or_else(|| parsed.as_ref().map(native_sections))
      .unwrap_or_default();
    let segments = parsed.as_ref().map(native_segments).unwrap_or_default();
    let symbols: Vec<TargetSymbol> = archive_analysis
      .as_ref()
      .map(|archive| archive.symbols.clone())
      .or_else(|| parsed.as_ref().map(native_symbols))
      .unwrap_or_default();
    let dynamic_symbols: Vec<TargetSymbol> = parsed.as_ref().map(native_dynamic_symbols).unwrap_or_default();
    let functions = archive_analysis
      .as_ref()
      .map(|archive| archive.functions.clone())
      .unwrap_or_else(|| derive_function_boundaries(&symbols, &dynamic_symbols));
    let relocations = archive_analysis
      .as_ref()
      .map(|archive| archive.relocations.clone())
      .or_else(|| parsed.as_ref().map(native_relocations))
      .unwrap_or_default();
    let imports = archive_analysis
      .as_ref()
      .map(|archive| archive.imports.clone())
      .or_else(|| parsed.as_ref().map(native_imports))
      .unwrap_or_default();
    let exports = archive_analysis
      .as_ref()
      .map(|archive| archive.exports.clone())
      .or_else(|| parsed.as_ref().map(native_exports))
      .unwrap_or_default();
    let debug = archive_analysis
      .as_ref()
      .map(|archive| archive.debug.clone())
      .or_else(|| parsed.as_ref().map(native_debug_evidence))
      .unwrap_or_else(DebugEvidence::empty);
    let toolchain = archive_analysis
      .as_ref()
      .map(|archive| archive.toolchain.clone())
      .or_else(|| parsed.as_ref().map(extract_toolchain_evidence))
      .unwrap_or_else(ToolchainEvidence::unknown);
    let platform_fingerprint = infer_platform_fingerprint(
      target,
      adapter,
      &architecture,
      target.file_kind_label().as_str(),
      pointer_width_bits,
      &toolchain,
      archive_analysis
        .as_ref()
        .and_then(|archive| archive.object_format.as_deref()),
    );

    let notes = if let Some(archive) = archive_analysis.as_ref() {
      let mut notes = vec![
        format!(
          "Parsed archive using Rust archive readers (kind={}, thin={}, members={}).",
          archive.archive_kind,
          archive.is_thin,
          archive.members.len()
        ),
        "Archive member inventories are evidence records, not recovered source."
          .to_string(),
      ];
      notes.extend(archive.notes.clone());
      notes
    } else if parsed.is_some() {
      vec![
        "Parsed target using Rust object readers.".to_string(),
        "Section, segment, symbol, relocation, dependency, debug, and toolchain inventories are evidence records, not recovered source."
          .to_string(),
      ]
    } else {
      vec!["Target format probe succeeded with limited structural parsing.".to_string()]
    };

    Self {
      schema_version: 1,
      generated_at: now_rfc3339(),
      case_id: target.case_id(),
      adapter: adapter.clone(),
      target: AnalysisTarget {
        path: target.path.display().to_string(),
        file_name: target.file_name(),
        file_kind: target.file_kind_label(),
        platform_fingerprint,
        size_bytes: target.bytes.len() as u64,
        sha256: target.sha256(),
        architecture,
        endianness,
        entry_point,
        build_id,
        sections,
        segments,
        symbols,
        dynamic_symbols,
        functions,
        relocations,
        imports,
        exports,
        archive_kind: archive_analysis
          .as_ref()
          .map(|archive| archive.archive_kind.clone()),
        archive_is_thin: archive_analysis.as_ref().map(|archive| archive.is_thin),
        archive_members: archive_analysis
          .as_ref()
          .map(|archive| archive.members.clone())
          .unwrap_or_default(),
        debug,
        toolchain,
      },
      tool_availability: build_tool_availability(adapter, repo_root),
      notes,
    }
  }
}

#[derive(Debug, Clone)]
struct ArchiveAnalysis {
  archive_kind: String,
  is_thin: bool,
  object_format: Option<String>,
  architecture: String,
  pointer_width_bits: Option<u8>,
  endianness: String,
  build_id: Option<String>,
  sections: Vec<TargetSection>,
  symbols: Vec<TargetSymbol>,
  functions: Vec<TargetFunction>,
  relocations: Vec<TargetRelocation>,
  imports: Vec<TargetImport>,
  exports: Vec<TargetExport>,
  members: Vec<TargetArchiveMember>,
  debug: DebugEvidence,
  toolchain: ToolchainEvidence,
  notes: Vec<String>,
}

fn native_sections(file: &File<'_>) -> Vec<TargetSection> {
  file
    .sections()
    .map(|section| TargetSection {
      name: section.name().unwrap_or("unknown").to_string(),
      size: section.size(),
      address: section.address(),
      kind: format!("{:?}", section.kind()),
    })
    .collect()
}

fn native_segments(file: &File<'_>) -> Vec<TargetSegment> {
  file
    .segments()
    .map(|segment| {
      let (file_offset, file_size) = segment.file_range();
      TargetSegment {
        name: segment.name().ok().flatten().unwrap_or("unknown").to_string(),
        address: segment.address(),
        size: segment.size(),
        align: segment.align(),
        file_offset,
        file_size,
        flags: format!("{:?}", segment.flags()),
      }
    })
    .collect()
}

fn native_symbols(file: &File<'_>) -> Vec<TargetSymbol> {
  file
    .symbols()
    .map(|symbol| TargetSymbol {
      name: symbol.name().unwrap_or("unknown").to_string(),
      address: symbol.address(),
      size: symbol.size(),
      kind: format!("{:?}", symbol.kind()),
      scope: format!("{:?}", symbol.scope()),
    })
    .collect()
}

fn native_dynamic_symbols(file: &File<'_>) -> Vec<TargetSymbol> {
  file
    .dynamic_symbols()
    .map(|symbol| TargetSymbol {
      name: symbol.name().unwrap_or("unknown").to_string(),
      address: symbol.address(),
      size: symbol.size(),
      kind: format!("{:?}", symbol.kind()),
      scope: format!("{:?}", symbol.scope()),
    })
    .collect()
}

fn native_relocations(file: &File<'_>) -> Vec<TargetRelocation> {
  file
    .sections()
    .flat_map(|section| {
      let section_name = section.name().unwrap_or("unknown").to_string();
      section.relocations().map(move |(offset, relocation)| {
        let target_label = match relocation.target() {
          RelocationTarget::Symbol(index) => file
            .symbol_by_index(index)
            .ok()
            .and_then(|symbol| symbol.name().ok().map(ToString::to_string))
            .unwrap_or_else(|| format!("symbol:{:?}", index.0)),
          RelocationTarget::Section(index) => file
            .section_by_index(index)
            .ok()
            .and_then(|section| section.name().ok().map(ToString::to_string))
            .unwrap_or_else(|| format!("section:{:?}", index.0)),
          RelocationTarget::Absolute => "absolute".to_string(),
          other => format!("{other:?}"),
        };

        TargetRelocation {
          section: section_name.clone(),
          offset,
          size: relocation.size(),
          kind: format!("{:?}", relocation.kind()),
          encoding: format!("{:?}", relocation.encoding()),
          target: target_label,
          addend: relocation.addend(),
        }
      })
    })
    .collect()
}

fn native_imports(file: &File<'_>) -> Vec<TargetImport> {
  file
    .imports()
    .ok()
    .map(|imports| {
      imports
        .into_iter()
        .map(|import| TargetImport {
          library: bytes_to_string(import.library()),
          name: bytes_to_string(import.name()),
        })
        .collect()
    })
    .unwrap_or_default()
}

fn native_exports(file: &File<'_>) -> Vec<TargetExport> {
  file
    .exports()
    .ok()
    .map(|exports| {
      exports
        .into_iter()
        .map(|export| TargetExport {
          name: bytes_to_string(export.name()),
          address: export.address(),
        })
        .collect()
    })
    .unwrap_or_default()
}

fn native_debug_evidence(file: &File<'_>) -> DebugEvidence {
  DebugEvidence {
    has_debug_symbols: file.has_debug_symbols(),
    gnu_debuglink: file
      .gnu_debuglink()
      .ok()
      .flatten()
      .map(|(file, crc)| GnuDebugLink {
        file: bytes_to_string(file),
        crc,
      }),
    gnu_debugaltlink: file
      .gnu_debugaltlink()
      .ok()
      .flatten()
      .map(|(file, build_id)| GnuDebugAltLink {
        file: bytes_to_string(file),
        build_id: hex::encode(build_id),
      }),
    mach_uuid: file.mach_uuid().ok().flatten().map(hex::encode),
  }
}

fn analyze_archive_members(data: &[u8]) -> Result<ArchiveAnalysis> {
  let archive = ArchiveFile::parse(data)?;
  let archive_kind = format!("{:?}", archive.kind());
  let is_thin = archive.is_thin();
  let mut object_formats = Vec::new();
  let mut architectures = Vec::new();
  let mut pointer_widths = Vec::new();
  let mut endiannesses = Vec::new();
  let mut sections = Vec::new();
  let mut symbols = Vec::new();
  let mut functions = Vec::new();
  let mut relocations = Vec::new();
  let mut imports = Vec::new();
  let mut exports = Vec::new();
  let mut members = Vec::new();
  let mut comment_strings = Vec::new();
  let mut toolchain_notes = Vec::new();
  let mut has_debug_symbols = false;

  for (index, member) in archive.members().enumerate() {
    let member = member?;
    let member_name = bytes_to_string(member.name());
    let member_id = format!(
      "archive-member-{}-{}",
      index,
      sanitize_identifier(&member_name)
    );
    let member_bytes = member.data(data).unwrap_or(&[]);
    let member_sha = hex::encode(Sha256::digest(member_bytes));
    let parsed_kind = object::FileKind::parse(member_bytes).ok();
    let parsed_file = File::parse(member_bytes).ok();
    let file_kind = parsed_kind
      .map(|kind| format!("{kind:?}"))
      .unwrap_or_else(|| "Unknown".to_string());
    let object_format = parsed_kind
      .map(|kind| canonical_object_format(&format!("{kind:?}")))
      .unwrap_or_else(|| "unknown".to_string());
    let architecture = parsed_file
      .as_ref()
      .map(|file| format!("{:?}", file.architecture()))
      .unwrap_or_else(|| "Unknown".to_string());
    let pointer_width_bits = parsed_file
      .as_ref()
      .and_then(|file| file.architecture().address_size())
      .map(|size| size.bytes() * 8);
    let endianness = parsed_file
      .as_ref()
      .map(|file| format!("{:?}", file.endianness()))
      .unwrap_or_else(|| "Unknown".to_string());

    let member_symbols = parsed_file
      .as_ref()
      .map(native_symbols)
      .unwrap_or_default();
    let member_functions = derive_function_boundaries(&member_symbols, &[]);
    let member_sections = parsed_file
      .as_ref()
      .map(native_sections)
      .unwrap_or_default();
    let member_relocations = parsed_file
      .as_ref()
      .map(native_relocations)
      .unwrap_or_default();
    let member_imports = parsed_file
      .as_ref()
      .map(native_imports)
      .unwrap_or_default();
    let member_exports = parsed_file
      .as_ref()
      .map(native_exports)
      .unwrap_or_default();

    if let Some(file) = parsed_file.as_ref() {
      let toolchain = extract_toolchain_evidence(file);
      comment_strings.extend(toolchain.comment_strings);
      toolchain_notes.extend(
        toolchain
          .notes
          .into_iter()
          .map(|note| format!("archive_member={member_name}:{note}")),
      );
      has_debug_symbols |= file.has_debug_symbols();
      object_formats.push(object_format.clone());
      architectures.push(architecture.clone());
      endiannesses.push(endianness.clone());
      if let Some(pointer_width_bits) = pointer_width_bits {
        pointer_widths.push(pointer_width_bits);
      }

      sections.extend(member_sections.iter().map(|section| TargetSection {
        name: format!("{member_name}:{}", section.name),
        size: section.size,
        address: section.address,
        kind: section.kind.clone(),
      }));
      symbols.extend(member_symbols.iter().map(|symbol| TargetSymbol {
        name: format!("{member_name}::{}", symbol.name),
        address: symbol.address,
        size: symbol.size,
        kind: symbol.kind.clone(),
        scope: symbol.scope.clone(),
      }));
      functions.extend(member_functions.iter().map(|function| TargetFunction {
        name: format!("{member_name}::{}", function.name),
        address: function.address,
        size: function.size,
        source: "archive-member-symbol".to_string(),
        confidence: function.confidence.clone(),
        cfg_status: function.cfg_status.clone(),
        evidence: function
          .evidence
          .iter()
          .cloned()
          .chain([format!("archive_member={member_name}")])
          .collect(),
      }));
      relocations.extend(member_relocations.iter().map(|relocation| TargetRelocation {
        section: format!("{member_name}:{}", relocation.section),
        offset: relocation.offset,
        size: relocation.size,
        kind: relocation.kind.clone(),
        encoding: relocation.encoding.clone(),
        target: format!("{member_name}:{}", relocation.target),
        addend: relocation.addend,
      }));
      imports.extend(member_imports.clone());
      exports.extend(member_exports.clone());
    }

    members.push(TargetArchiveMember {
      id: member_id,
      name: member_name.clone(),
      file_kind,
      architecture,
      endianness,
      size_bytes: member.size(),
      sha256: member_sha,
      object_format,
      parser_status: if parsed_file.is_some() {
        "parsed-object-member".to_string()
      } else if member.is_thin() {
        "thin-member-unavailable".to_string()
      } else {
        "opaque-member".to_string()
      },
      is_thin: member.is_thin(),
      section_count: member_sections.len(),
      symbol_count: member_symbols.len(),
      function_count: member_functions.len(),
      relocation_count: member_relocations.len(),
      import_count: member_imports.len(),
      export_count: member_exports.len(),
      date: member.date(),
      uid: member.uid(),
      gid: member.gid(),
      mode: member.mode(),
      evidence: vec![
        format!("archive_kind={archive_kind}"),
        format!("thin_member={}", member.is_thin()),
      ],
    });
  }

  comment_strings.sort();
  comment_strings.dedup();
  toolchain_notes.sort();
  toolchain_notes.dedup();

  Ok(ArchiveAnalysis {
    archive_kind,
    is_thin,
    object_format: dominant_string(&object_formats),
    architecture: dominant_string(&architectures).unwrap_or_else(|| "Unknown".to_string()),
    pointer_width_bits: dominant_u8(&pointer_widths),
    endianness: dominant_string(&endiannesses).unwrap_or_else(|| "Unknown".to_string()),
    build_id: None,
    sections,
    symbols,
    functions,
    relocations,
    imports,
    exports,
    members,
    debug: DebugEvidence {
      has_debug_symbols,
      gnu_debuglink: None,
      gnu_debugaltlink: None,
      mach_uuid: None,
    },
    toolchain: ToolchainEvidence {
      compiler: classify_toolchain_component(&comment_strings, &["gcc", "clang", "msvc", "rustc"]),
      linker: "unknown".to_string(),
      comment_strings,
      notes: toolchain_notes,
    },
    notes: vec![
      "Static library analysis is object-member oriented by default; top-level archive/package proof preserves common ar container evidence for non-thin archives when raw members align with build units.".to_string(),
      "Archive members produce truthful multi-unit reconstruction scaffolding without claiming recovered source.".to_string(),
    ],
  })
}

fn dominant_string(values: &[String]) -> Option<String> {
  dominant_by_count(values.iter().filter(|value| !value.is_empty() && *value != "Unknown").cloned())
}

fn dominant_u8(values: &[u8]) -> Option<u8> {
  dominant_by_count(values.iter().copied())
}

fn dominant_by_count<T>(values: impl Iterator<Item = T>) -> Option<T>
where
  T: Ord + Clone,
{
  let mut counts = BTreeMap::<T, usize>::new();
  for value in values {
    *counts.entry(value).or_insert(0) += 1;
  }
  counts
    .into_iter()
    .max_by(|left, right| left.1.cmp(&right.1).then_with(|| left.0.cmp(&right.0)))
    .map(|(value, _)| value)
}

fn build_tool_availability(adapter: &AdapterDescriptor, repo_root: &Path) -> ToolAvailability {
  let analysis_providers = adapter
    .analysis_providers
    .iter()
    .map(|provider| provider_availability(provider, repo_root))
    .collect::<Vec<_>>();
  let ghidra_headless = analysis_providers
    .iter()
    .find(|provider| provider.id == "ghidra")
    .map(|provider| provider.available)
    .unwrap_or(false);

  ToolAvailability {
    objdiff: command_exists("objdiff"),
    ghidra_headless,
    compile_placeholder: repo_root.join("scripts/compile-placeholder.sh").is_file(),
    analysis_providers,
  }
}

fn provider_availability(
  provider: &AnalysisProvider,
  repo_root: &Path,
) -> AnalysisProviderAvailability {
  match provider.id.as_str() {
    "ghidra" => AnalysisProviderAvailability {
      id: provider.id.clone(),
      role: provider.role.clone(),
      kind: "command".to_string(),
      available: command_exists("analyzeHeadless"),
      detail: "Requires `analyzeHeadless` on PATH for scripted headless Ghidra analysis.".to_string(),
      evidence: vec!["command=analyzeHeadless".to_string()],
    },
    "agdec-http" => AnalysisProviderAvailability {
      id: provider.id.clone(),
      role: provider.role.clone(),
      kind: "workspace-config".to_string(),
      available: workspace_mcp_server_configured(repo_root, "agdec-http"),
      detail: "Requires the `agdec-http` MCP server to be configured in the workspace.".to_string(),
      evidence: vec![
        "file=.cursor/mcp.json".to_string(),
        "server=agdec-http".to_string(),
      ],
    },
    "native-object" => AnalysisProviderAvailability {
      id: provider.id.clone(),
      role: provider.role.clone(),
      kind: "in-process".to_string(),
      available: true,
      detail: "Uses Rust object readers linked into the current runtime.".to_string(),
      evidence: vec!["crate=object".to_string()],
    },
    "raw-inspector" => AnalysisProviderAvailability {
      id: provider.id.clone(),
      role: provider.role.clone(),
      kind: "in-process".to_string(),
      available: true,
      detail: "Uses in-process raw byte inspection for probe-only blob analysis.".to_string(),
      evidence: vec!["mode=raw-byte-probe".to_string()],
    },
    _ => AnalysisProviderAvailability {
      id: provider.id.clone(),
      role: provider.role.clone(),
      kind: "unknown".to_string(),
      available: false,
      detail: "Provider availability is not modeled yet.".to_string(),
      evidence: vec!["status=unmodeled-provider".to_string()],
    },
  }
}

fn workspace_mcp_server_configured(repo_root: &Path, server_id: &str) -> bool {
  let mcp_path = repo_root.join(".cursor/mcp.json");
  let Ok(contents) = fs::read_to_string(mcp_path) else {
    return false;
  };
  let Ok(parsed) = serde_json::from_str::<Value>(&contents) else {
    return false;
  };
  parsed
    .get("mcpServers")
    .and_then(|servers| servers.get(server_id))
    .is_some()
}

fn infer_platform_fingerprint(
  target: &TargetInput,
  adapter: &AdapterDescriptor,
  architecture: &str,
  file_kind: &str,
  pointer_width_bits: Option<u8>,
  toolchain: &ToolchainEvidence,
  object_format_hint: Option<&str>,
) -> TargetPlatformFingerprint {
  let object_format = object_format_hint
    .filter(|hint| !hint.is_empty() && *hint != "unknown")
    .map(ToString::to_string)
    .unwrap_or_else(|| canonical_object_format(file_kind));
  let vendor = infer_vendor(adapter, toolchain);
  let operating_system = infer_operating_system(adapter, file_kind);
  let environment = infer_environment(adapter, toolchain);
  let binary_interface_hypotheses = infer_binary_interface_hypotheses(
    adapter,
    architecture,
    &object_format,
    &environment,
    target,
  );
  let triple_candidates = infer_triple_candidates(
    architecture,
    &vendor,
    &operating_system,
    &environment,
    &object_format,
  );

  TargetPlatformFingerprint {
    object_format,
    pointer_width_bits,
    vendor,
    operating_system,
    environment,
    binary_interface_hypotheses,
    triple_candidates,
  }
}

fn canonical_object_format(file_kind: &str) -> String {
  let lower = file_kind.to_ascii_lowercase();
  if lower.contains("elf") {
    "elf".to_string()
  } else if lower.contains("pe") || lower.contains("coff") {
    "coff".to_string()
  } else if lower.contains("macho") {
    "macho".to_string()
  } else if lower.contains("archive") {
    "archive".to_string()
  } else {
    "unknown".to_string()
  }
}

fn infer_vendor(adapter: &AdapterDescriptor, toolchain: &ToolchainEvidence) -> String {
  match adapter.platform.as_str() {
    "windows" => "pc".to_string(),
    "macos" => "apple".to_string(),
    "ps2" => "scei".to_string(),
    "xbox" => "microsoft".to_string(),
    _ => {
      let compiler = toolchain.compiler.to_ascii_lowercase();
      if compiler.contains("apple") {
        "apple".to_string()
      } else if compiler.contains("msvc") || compiler.contains("microsoft") {
        "pc".to_string()
      } else {
        "unknown".to_string()
      }
    }
  }
}

fn infer_operating_system(adapter: &AdapterDescriptor, file_kind: &str) -> String {
  match adapter.platform.as_str() {
    "windows" => "windows".to_string(),
    "macos" => "macos".to_string(),
    "ps2" => "ps2".to_string(),
    "xbox" => "xbox".to_string(),
    "unix" if file_kind.to_ascii_lowercase().contains("elf") => "unknown".to_string(),
    other if !other.is_empty() && other != "unknown" => other.to_string(),
    _ => "unknown".to_string(),
  }
}

fn infer_environment(adapter: &AdapterDescriptor, toolchain: &ToolchainEvidence) -> String {
  let evidence = format!(
    "{}\n{}\n{}",
    toolchain.compiler,
    toolchain.linker,
    toolchain.comment_strings.join("\n")
  )
  .to_ascii_lowercase();

  if evidence.contains("musl") {
    "musl".to_string()
  } else if evidence.contains("gnu") || evidence.contains("gcc") {
    "gnu".to_string()
  } else if evidence.contains("msvc") || evidence.contains("microsoft") {
    "msvc".to_string()
  } else if evidence.contains("clang-cl") {
    "msvc".to_string()
  } else if adapter.platform == "windows" {
    "unknown-windows".to_string()
  } else {
    "unknown".to_string()
  }
}

fn infer_binary_interface_hypotheses(
  adapter: &AdapterDescriptor,
  architecture: &str,
  object_format: &str,
  environment: &str,
  target: &TargetInput,
) -> Vec<String> {
  let arch = architecture.to_ascii_lowercase();
  let mut values = Vec::new();

  match object_format {
    "elf" => values.push("sysv-elf".to_string()),
    "coff" => values.push("pe-coff".to_string()),
    "macho" => values.push("apple-macho".to_string()),
    "archive" => values.push("archive-member-interface".to_string()),
    _ => {}
  }

  if adapter.platform == "windows" && arch.contains("x86_64") {
    values.push("microsoft-x64-calling-convention-family".to_string());
  }
  if adapter.platform == "windows" && arch == "i386" {
    values.push("win32-x86-calling-convention-family".to_string());
  }
  if adapter.platform == "ps2" && arch.contains("mips") {
    values.push("ps2-ee-mips-elf".to_string());
  }
  if adapter.platform == "xbox" && arch == "i386" {
    values.push("xbox-x86-pe".to_string());
  }
  if environment == "msvc" {
    values.push("msvc-runtime-family".to_string());
  }
  if environment == "gnu" {
    values.push("gnu-runtime-family".to_string());
  }
  if target.file_name().to_ascii_lowercase().ends_with(".dll") {
    values.push("shared-library-entry-contract".to_string());
  }

  values.sort();
  values.dedup();
  if values.is_empty() {
    values.push("unknown-binary-interface".to_string());
  }
  values
}

fn infer_triple_candidates(
  architecture: &str,
  vendor: &str,
  operating_system: &str,
  environment: &str,
  object_format: &str,
) -> Vec<String> {
  let arch = canonical_arch_name(architecture);
  let vendor = if vendor.is_empty() { "unknown" } else { vendor };
  let os = if operating_system.is_empty() {
    "unknown"
  } else {
    operating_system
  };
  let env = if environment.is_empty() {
    "unknown"
  } else {
    environment
  };

  let mut candidates = vec![format!("{arch}-{vendor}-{os}-{env}")];
  if env == "unknown" {
    candidates.push(format!("{arch}-{vendor}-{os}"));
  }
  if os == "unknown" && object_format != "unknown" {
    candidates.push(format!("{arch}-{vendor}-unknown-{object_format}"));
  }
  candidates.sort();
  candidates.dedup();
  candidates
}

fn canonical_arch_name(architecture: &str) -> String {
  match architecture.to_ascii_lowercase().as_str() {
    "x86_64" => "x86_64".to_string(),
    "i386" => "i686".to_string(),
    "aarch64" => "aarch64".to_string(),
    "arm" => "arm".to_string(),
    "mips" => "mips".to_string(),
    "mips64" => "mips64".to_string(),
    "powerpc" => "powerpc".to_string(),
    "powerpc64" => "powerpc64".to_string(),
    "riscv32" => "riscv32".to_string(),
    "riscv64" => "riscv64".to_string(),
    other => other.to_string(),
  }
}

fn derive_function_boundaries(
  symbols: &[TargetSymbol],
  dynamic_symbols: &[TargetSymbol],
) -> Vec<TargetFunction> {
  let mut functions = BTreeMap::<(u64, String), TargetFunction>::new();
  for (source, symbol) in symbols
    .iter()
    .map(|symbol| ("symbol-table", symbol))
    .chain(dynamic_symbols.iter().map(|symbol| ("dynamic-symbol-table", symbol)))
  {
    if symbol.kind != "Text" {
      continue;
    }
    let confidence = if symbol.size > 0 && symbol.name != "unknown" {
      "medium"
    } else {
      "low"
    };
    functions
      .entry((symbol.address, symbol.name.clone()))
      .or_insert_with(|| TargetFunction {
        name: symbol.name.clone(),
        address: symbol.address,
        size: symbol.size,
        source: source.to_string(),
        confidence: confidence.to_string(),
        cfg_status: "not-recovered".to_string(),
        evidence: vec![
          format!("symbol_kind={}", symbol.kind),
          format!("symbol_scope={}", symbol.scope),
          format!("symbol_size={}", symbol.size),
          format!("source={source}"),
        ],
      });
  }
  functions.into_values().collect()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReconstructionGraph {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub state: String,
  #[serde(rename = "sourceCandidates")]
  pub source_candidates: Vec<SourceCandidate>,
  pub sections: Vec<TargetSection>,
  pub segments: Vec<TargetSegment>,
  pub symbols: Vec<TargetSymbol>,
  #[serde(rename = "dynamicSymbols")]
  pub dynamic_symbols: Vec<TargetSymbol>,
  pub functions: Vec<TargetFunction>,
  pub relocations: Vec<TargetRelocation>,
  pub imports: Vec<TargetImport>,
  pub exports: Vec<TargetExport>,
  #[serde(rename = "projectStructure")]
  pub project_structure: ProjectStructure,
  pub debug: DebugEvidence,
  pub toolchain: ToolchainEvidence,
  #[serde(rename = "toolchainHypothesis")]
  pub toolchain_hypothesis: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectStructure {
  #[serde(rename = "sourceRoots")]
  pub source_roots: Vec<String>,
  #[serde(rename = "includeRoots")]
  pub include_roots: Vec<String>,
  #[serde(rename = "buildRoots")]
  pub build_roots: Vec<String>,
  #[serde(rename = "artifactRoots")]
  pub artifact_roots: Vec<String>,
  #[serde(rename = "translationUnits")]
  pub translation_units: Vec<TranslationUnit>,
  #[serde(rename = "linkUnits")]
  pub link_units: Vec<LinkUnit>,
  pub notes: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranslationUnit {
  pub id: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  #[serde(rename = "objectPath")]
  pub object_path: String,
  pub language: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "compilerProfileCandidates")]
  pub compiler_profile_candidates: Vec<String>,
  #[serde(rename = "blockingReasons")]
  pub blocking_reasons: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkUnit {
  pub id: String,
  #[serde(rename = "artifactPath")]
  pub artifact_path: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "linkerProfileCandidates")]
  pub linker_profile_candidates: Vec<String>,
  #[serde(rename = "dependencyLibraries")]
  pub dependency_libraries: Vec<String>,
  #[serde(rename = "linkInputs")]
  pub link_inputs: Vec<LinkInputPlan>,
  #[serde(rename = "runtimeArtifacts")]
  pub runtime_artifacts: Vec<RuntimeArtifactPlan>,
  #[serde(rename = "blockingReasons")]
  pub blocking_reasons: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceCandidate {
  pub path: String,
  pub language: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "blockingReasons")]
  pub blocking_reasons: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildPlan {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub state: String,
  #[serde(rename = "compilerScript")]
  pub compiler_script: String,
  #[serde(rename = "compilerScriptRoot")]
  pub compiler_script_root: String,
  #[serde(rename = "compilerConfigPath")]
  pub compiler_config_path: Option<String>,
  #[serde(rename = "candidateSource")]
  pub candidate_source: String,
  #[serde(rename = "candidateObject")]
  pub candidate_object: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "rebuildSupported")]
  pub rebuild_supported: bool,
  #[serde(rename = "sourceLanguage")]
  pub source_language: String,
  #[serde(rename = "targetFormat")]
  pub target_format: String,
  #[serde(rename = "targetArchitecture")]
  pub target_architecture: String,
  #[serde(rename = "expectedArtifact")]
  pub expected_artifact: BuildArtifactSpec,
  #[serde(rename = "buildSystem")]
  pub build_system: BuildSystemPlan,
  pub toolchain: BuildToolchainPlan,
  #[serde(rename = "linkPlan")]
  pub link_plan: LinkPlan,
  #[serde(rename = "buildUnits")]
  pub build_units: Vec<BuildUnitPlan>,
  pub dependencies: Vec<BuildDependencyPlan>,
  #[serde(rename = "requiredInputs")]
  pub required_inputs: Vec<BuildInputRequirement>,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildArtifactSpec {
  pub path: String,
  pub kind: String,
  pub comparator: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildSystemPlan {
  pub kind: String,
  pub executable: bool,
  pub reason: String,
  #[serde(rename = "preferredBackend")]
  pub preferred_backend: Option<String>,
  #[serde(rename = "candidateBackends")]
  pub candidate_backends: Vec<BuildBackendPlan>,
  #[serde(rename = "generatedArtifacts")]
  pub generated_artifacts: Vec<GeneratedBuildArtifact>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildBackendPlan {
  pub id: String,
  pub family: String,
  pub generator: String,
  pub status: String,
  pub evidence: Vec<String>,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GeneratedBuildArtifact {
  pub path: String,
  pub kind: String,
  pub backend: String,
  pub executable: bool,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildToolchainPlan {
  pub compiler: String,
  pub linker: String,
  pub status: String,
  pub evidence: Vec<String>,
  pub stages: Vec<ToolchainStagePlan>,
  #[serde(rename = "rankingStatus")]
  pub ranking_status: String,
  #[serde(rename = "recommendedProfile")]
  pub recommended_profile: Option<String>,
  #[serde(rename = "selectedProfile")]
  pub selected_profile: Option<String>,
  #[serde(rename = "candidateProfiles")]
  pub candidate_profiles: Vec<CompilerProfile>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolchainStagePlan {
  pub id: String,
  pub name: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "candidateProfiles")]
  pub candidate_profiles: Vec<String>,
  #[serde(rename = "requiredComponents")]
  pub required_components: Vec<String>,
  pub evidence: Vec<String>,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerProfile {
  pub id: String,
  pub family: String,
  pub vendor: String,
  pub role: String,
  pub status: String,
  #[serde(rename = "evidenceScore")]
  pub evidence_score: u32,
  #[serde(rename = "evidenceConfidence")]
  pub evidence_confidence: String,
  pub evidence: Vec<String>,
  #[serde(rename = "rankingReasons")]
  pub ranking_reasons: Vec<String>,
  #[serde(rename = "upstreamEvidence")]
  pub upstream_evidence: Vec<UpstreamSourceEvidence>,
  #[serde(rename = "requiredComponents")]
  pub required_components: Vec<CompilerComponentRequirement>,
  pub uncertainty: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerComponentRequirement {
  pub name: String,
  pub kind: String,
  pub status: String,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
pub struct UpstreamSourceEvidence {
  pub system: String,
  pub role: String,
  #[serde(rename = "appliesToProfiles")]
  pub applies_to_profiles: Vec<String>,
  pub repo: String,
  pub path: String,
  pub revision: String,
  #[serde(rename = "sourceKind")]
  pub source_kind: String,
  #[serde(rename = "sourceSha")]
  pub source_sha: String,
  #[serde(rename = "apiUrl")]
  pub api_url: String,
  #[serde(rename = "gitUrl")]
  pub git_url: String,
  #[serde(rename = "htmlUrl")]
  pub html_url: String,
  #[serde(rename = "downloadUrl")]
  pub download_url: String,
  pub verification: String,
  pub rationale: String,
  #[serde(rename = "rustPortStatus")]
  pub rust_port_status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerCompatibilityLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "profileCount")]
  pub profile_count: usize,
  #[serde(rename = "publicSourceModeledCount")]
  pub public_source_modeled_count: usize,
  #[serde(rename = "proprietaryGapCount")]
  pub proprietary_gap_count: usize,
  #[serde(rename = "exactInvocationRecovered")]
  pub exact_invocation_recovered: bool,
  #[serde(rename = "selectedProfile")]
  pub selected_profile: Option<String>,
  #[serde(rename = "recommendedProfile")]
  pub recommended_profile: Option<String>,
  pub profiles: Vec<CompilerCompatibilityProfile>,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerCompatibilityProfile {
  pub id: String,
  pub family: String,
  pub vendor: String,
  pub status: String,
  #[serde(rename = "compatibilityStatus")]
  pub compatibility_status: String,
  #[serde(rename = "sourceAvailability")]
  pub source_availability: String,
  #[serde(rename = "modelingBoundary")]
  pub modeling_boundary: String,
  #[serde(rename = "exactInvocationStatus")]
  pub exact_invocation_status: String,
  #[serde(rename = "evidenceScore")]
  pub evidence_score: u32,
  #[serde(rename = "evidenceConfidence")]
  pub evidence_confidence: String,
  #[serde(rename = "sourceSystems")]
  pub source_systems: Vec<String>,
  #[serde(rename = "rustPortStatuses")]
  pub rust_port_statuses: Vec<String>,
  #[serde(rename = "requiredComponents")]
  pub required_components: Vec<CompilerComponentRequirement>,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildDependencyPlan {
  pub library: String,
  pub symbol: String,
  pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkPlan {
  #[serde(rename = "artifactPath")]
  pub artifact_path: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "linkerProfileCandidates")]
  pub linker_profile_candidates: Vec<String>,
  pub inputs: Vec<LinkInputPlan>,
  #[serde(rename = "runtimeArtifacts")]
  pub runtime_artifacts: Vec<RuntimeArtifactPlan>,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LinkInputPlan {
  pub name: String,
  pub kind: String,
  pub source: String,
  pub status: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeArtifactPlan {
  pub name: String,
  pub kind: String,
  pub status: String,
  pub detail: String,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildInputRequirement {
  pub name: String,
  pub status: String,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildUnitPlan {
  pub id: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  #[serde(rename = "objectPath")]
  pub object_path: String,
  pub language: String,
  pub status: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "proofTargetStatus")]
  pub proof_target_status: String,
  #[serde(rename = "proofTargetLocator")]
  pub proof_target_locator: String,
  #[serde(rename = "proofSourcePath")]
  pub proof_source_path: String,
  #[serde(rename = "proofTargetMemberIndex")]
  pub proof_target_member_index: Option<usize>,
  #[serde(rename = "compilerProfileCandidates")]
  pub compiler_profile_candidates: Vec<String>,
  #[serde(rename = "dependencySymbols")]
  pub dependency_symbols: Vec<String>,
  #[serde(rename = "requiredInputs")]
  pub required_inputs: Vec<String>,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerInvocationLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "candidateCount")]
  pub candidate_count: usize,
  #[serde(rename = "recoveredInvocationCount")]
  pub recovered_invocation_count: usize,
  #[serde(rename = "missingEvidence")]
  pub missing_evidence: Vec<String>,
  pub invocations: Vec<CompilerInvocationCandidate>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompilerInvocationCandidate {
  pub id: String,
  #[serde(rename = "profileId")]
  pub profile_id: String,
  #[serde(rename = "buildUnitId")]
  pub build_unit_id: String,
  pub language: String,
  pub status: String,
  #[serde(rename = "exactCommandRecovered")]
  pub exact_command_recovered: bool,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  #[serde(rename = "objectPath")]
  pub object_path: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "toolCandidates")]
  pub tool_candidates: Vec<InvocationToolCandidate>,
  #[serde(rename = "argumentVector")]
  pub argument_vector: Vec<String>,
  pub environment: Vec<InvocationEnvironmentRequirement>,
  #[serde(rename = "requiredEvidence")]
  pub required_evidence: Vec<String>,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProofTargetLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  pub comparator: String,
  #[serde(rename = "collectionKind")]
  pub collection_kind: String,
  #[serde(rename = "unitCount")]
  pub unit_count: usize,
  #[serde(rename = "mappedUnitCount")]
  pub mapped_unit_count: usize,
  #[serde(rename = "unavailableUnitCount")]
  pub unavailable_unit_count: usize,
  pub units: Vec<ProofTargetUnit>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProofTargetUnit {
  #[serde(rename = "buildUnitId")]
  pub build_unit_id: String,
  #[serde(rename = "proofSourcePath")]
  pub proof_source_path: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  pub kind: String,
  pub status: String,
  pub locator: String,
  #[serde(rename = "memberIndex")]
  pub member_index: Option<usize>,
  #[serde(rename = "memberName")]
  pub member_name: Option<String>,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationToolCandidate {
  pub component: String,
  pub kind: String,
  #[serde(rename = "commandCandidates")]
  pub command_candidates: Vec<String>,
  #[serde(rename = "installedCandidates")]
  pub installed_candidates: Vec<CommandAvailability>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandAvailability {
  pub name: String,
  pub installed: bool,
  #[serde(rename = "resolvedPath")]
  pub resolved_path: Option<String>,
  #[serde(rename = "probeStatus")]
  pub probe_status: String,
  #[serde(rename = "versionProbe")]
  pub version_probe: Option<String>,
  #[serde(rename = "versionOutput")]
  pub version_output: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvocationEnvironmentRequirement {
  pub name: String,
  pub status: String,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceAuditRecord {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "artifactCount")]
  pub artifact_count: usize,
  #[serde(rename = "blockedStubCount")]
  pub blocked_stub_count: usize,
  #[serde(rename = "suspiciousCount")]
  pub suspicious_count: usize,
  pub artifacts: Vec<SourceArtifactAudit>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceArtifactAudit {
  pub path: String,
  pub language: String,
  pub kind: String,
  pub verdict: String,
  #[serde(rename = "markedBlocking")]
  pub marked_blocking: bool,
  #[serde(rename = "compileBlocking")]
  pub compile_blocking: bool,
  #[serde(rename = "containsHardcodedAddress")]
  pub contains_hardcoded_address: bool,
  #[serde(rename = "containsUnmarkedPlaceholder")]
  pub contains_unmarked_placeholder: bool,
  #[serde(rename = "containsFabricationMarker")]
  pub contains_fabrication_marker: bool,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceVerificationLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "artifactCount")]
  pub artifact_count: usize,
  #[serde(rename = "verifiedSourceCount")]
  pub verified_source_count: usize,
  #[serde(rename = "byteProvedCandidateCount")]
  pub byte_proved_candidate_count: usize,
  #[serde(rename = "unverifiedSourceCount")]
  pub unverified_source_count: usize,
  #[serde(rename = "blockedStubCount")]
  pub blocked_stub_count: usize,
  #[serde(rename = "policyViolationCount")]
  pub policy_violation_count: usize,
  #[serde(rename = "exactInvocationRecoveredCount")]
  pub exact_invocation_recovered_count: usize,
  #[serde(rename = "proofAttributedCount")]
  pub proof_attributed_count: usize,
  pub artifacts: Vec<SourceVerificationArtifact>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceVerificationArtifact {
  pub path: String,
  pub language: String,
  pub kind: String,
  pub status: String,
  #[serde(rename = "auditVerdict")]
  pub audit_verdict: String,
  #[serde(rename = "sourceCandidateStatus")]
  pub source_candidate_status: String,
  #[serde(rename = "buildUnitId")]
  pub build_unit_id: Option<String>,
  #[serde(rename = "proofAttributed")]
  pub proof_attributed: bool,
  #[serde(rename = "rebuildStatus")]
  pub rebuild_status: String,
  #[serde(rename = "objectMatchStatus")]
  pub object_match_status: String,
  #[serde(rename = "binaryDiffStatus")]
  pub binary_diff_status: String,
  #[serde(rename = "compilerInvocationStatus")]
  pub compiler_invocation_status: String,
  #[serde(rename = "exactInvocationRecovered")]
  pub exact_invocation_recovered: bool,
  #[serde(rename = "byteEquivalentProof")]
  pub byte_equivalent_proof: bool,
  #[serde(rename = "verifiedRecoveredSource")]
  pub verified_recovered_source: bool,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildUnitVerificationLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "unitCount")]
  pub unit_count: usize,
  #[serde(rename = "proofAttributedCount")]
  pub proof_attributed_count: usize,
  #[serde(rename = "verifiedUnitCount")]
  pub verified_unit_count: usize,
  #[serde(rename = "byteProvedUnitCount")]
  pub byte_proved_unit_count: usize,
  #[serde(rename = "unverifiedUnitCount")]
  pub unverified_unit_count: usize,
  #[serde(rename = "proofUnavailableCount")]
  pub proof_unavailable_count: usize,
  pub units: Vec<BuildUnitVerificationUnit>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildUnitVerificationUnit {
  pub id: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  #[serde(rename = "objectPath")]
  pub object_path: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  pub status: String,
  #[serde(rename = "proofAttributed")]
  pub proof_attributed: bool,
  #[serde(rename = "rebuildStatus")]
  pub rebuild_status: String,
  #[serde(rename = "objectMatchStatus")]
  pub object_match_status: String,
  #[serde(rename = "binaryDiffStatus")]
  pub binary_diff_status: String,
  #[serde(rename = "compilerInvocationStatus")]
  pub compiler_invocation_status: String,
  #[serde(rename = "exactInvocationRecovered")]
  pub exact_invocation_recovered: bool,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildUnitProofResult {
  #[serde(rename = "buildUnitId")]
  pub build_unit_id: String,
  #[serde(rename = "sourcePath")]
  pub source_path: String,
  #[serde(rename = "objectPath")]
  pub object_path: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "proofTargetStatus")]
  pub proof_target_status: String,
  #[serde(rename = "rebuildStatus")]
  pub rebuild_status: String,
  #[serde(rename = "rebuildDetail")]
  pub rebuild_detail: String,
  #[serde(rename = "objectMatchStatus")]
  pub object_match_status: String,
  #[serde(rename = "objectMatchDetail")]
  pub object_match_detail: String,
  #[serde(rename = "binaryDiffStatus")]
  pub binary_diff_status: String,
  #[serde(rename = "binaryDiffDetail")]
  pub binary_diff_detail: String,
  #[serde(rename = "artifactComparison")]
  pub artifact_comparison: Option<ArtifactComparison>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureClass {
  UnsupportedFormat,
  ToolMissing,
  AnalysisTimeout,
  CompilerUnknown,
  ProofArtifactMissing,
  RebuildFailed,
  VerificationMismatch,
  SemanticUnknown,
  DecompilationDrift,
  InfraError,
}

impl fmt::Display for FailureClass {
  fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
    let value = match self {
      FailureClass::UnsupportedFormat => "unsupported_format",
      FailureClass::ToolMissing => "tool_missing",
      FailureClass::AnalysisTimeout => "analysis_timeout",
      FailureClass::CompilerUnknown => "compiler_unknown",
      FailureClass::ProofArtifactMissing => "proof_artifact_missing",
      FailureClass::RebuildFailed => "rebuild_failed",
      FailureClass::VerificationMismatch => "verification_mismatch",
      FailureClass::SemanticUnknown => "semantic_unknown",
      FailureClass::DecompilationDrift => "decompilation_drift",
      FailureClass::InfraError => "infra_error",
    };
    write!(f, "{value}")
  }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BlockerDomain {
  Infra,
  Proof,
  Recovery,
  Verification,
}

impl fmt::Display for BlockerDomain {
  fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
    let value = match self {
      BlockerDomain::Infra => "infra",
      BlockerDomain::Proof => "proof",
      BlockerDomain::Recovery => "recovery",
      BlockerDomain::Verification => "verification",
    };
    write!(f, "{value}")
  }
}

impl FailureClass {
  pub fn blocker_domain(&self) -> BlockerDomain {
    match self {
      FailureClass::ToolMissing | FailureClass::AnalysisTimeout | FailureClass::InfraError => {
        BlockerDomain::Infra
      }
      FailureClass::ProofArtifactMissing => BlockerDomain::Proof,
      FailureClass::CompilerUnknown
      | FailureClass::RebuildFailed
      | FailureClass::SemanticUnknown
      | FailureClass::UnsupportedFormat => BlockerDomain::Recovery,
      FailureClass::VerificationMismatch | FailureClass::DecompilationDrift => {
        BlockerDomain::Verification
      }
    }
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationRecord {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "failureClasses")]
  pub failure_classes: Vec<FailureClass>,
  pub checks: Vec<VerificationCheck>,
  #[serde(rename = "artifactComparison")]
  pub artifact_comparison: Option<ArtifactComparison>,
  #[serde(rename = "buildUnitProofResults")]
  pub build_unit_proof_results: Vec<BuildUnitProofResult>,
  #[serde(rename = "matchScore")]
  pub match_score: MatchScore,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationCheck {
  pub name: String,
  pub status: String,
  pub advisory: bool,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MatchScore {
  #[serde(rename = "totalScore")]
  pub total_score: f64,
  #[serde(rename = "maxScore")]
  pub max_score: f64,
  pub normalized: f64,
  pub status: String,
  pub components: Vec<MatchScoreComponent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MatchScoreComponent {
  pub name: String,
  pub weight: f64,
  pub score: f64,
  pub status: String,
  pub advisory: bool,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationMatrix {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  pub summary: VerificationMatrixSummary,
  pub rows: Vec<VerificationMatrixRow>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationMatrixSummary {
  #[serde(rename = "authoritativeTotal")]
  pub authoritative_total: usize,
  #[serde(rename = "authoritativePassed")]
  pub authoritative_passed: usize,
  #[serde(rename = "authoritativeFailed")]
  pub authoritative_failed: usize,
  #[serde(rename = "advisoryTotal")]
  pub advisory_total: usize,
  #[serde(rename = "advisoryPassed")]
  pub advisory_passed: usize,
  #[serde(rename = "advisoryFailed")]
  pub advisory_failed: usize,
  #[serde(rename = "policyTotal")]
  pub policy_total: usize,
  #[serde(rename = "policyPassed")]
  pub policy_passed: usize,
  #[serde(rename = "policyFailed")]
  pub policy_failed: usize,
  #[serde(rename = "blockedRows")]
  pub blocked_rows: usize,
  #[serde(rename = "coverageStatus")]
  pub coverage_status: String,
  #[serde(rename = "scoreStatus")]
  pub score_status: String,
  #[serde(rename = "normalizedScore")]
  pub normalized_score: f64,
  #[serde(rename = "blockerDomains")]
  pub blocker_domains: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationMatrixRow {
  pub name: String,
  pub domain: String,
  pub authority: String,
  pub status: String,
  pub weight: f64,
  pub score: f64,
  pub blocking: bool,
  pub artifact: String,
  #[serde(rename = "failureClass")]
  pub failure_class: Option<FailureClass>,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoundTripProof {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "byteEquivalent")]
  pub byte_equivalent: bool,
  #[serde(rename = "proofChainComplete")]
  pub proof_chain_complete: bool,
  #[serde(rename = "candidateSource")]
  pub candidate_source: String,
  #[serde(rename = "candidateArtifact")]
  pub candidate_artifact: String,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  pub stages: Vec<RoundTripStage>,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoundTripStage {
  pub name: String,
  pub status: String,
  pub authoritative: bool,
  pub artifact: String,
  pub detail: String,
  pub blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriftAnalysis {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "driftCount")]
  pub drift_count: usize,
  #[serde(rename = "blockingDriftCount")]
  pub blocking_drift_count: usize,
  pub categories: Vec<DriftCategorySummary>,
  pub items: Vec<DriftItem>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriftCategorySummary {
  pub category: String,
  pub count: usize,
  #[serde(rename = "blockingCount")]
  pub blocking_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriftItem {
  pub id: String,
  pub category: String,
  #[serde(rename = "failureClass")]
  pub failure_class: FailureClass,
  pub status: String,
  pub severity: String,
  pub blocking: bool,
  pub summary: String,
  #[serde(rename = "sourceArtifact")]
  pub source_artifact: String,
  #[serde(rename = "expectedProof")]
  pub expected_proof: String,
  pub blockers: Vec<String>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ByteEquivalenceLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub status: String,
  #[serde(rename = "byteEquivalent")]
  pub byte_equivalent: bool,
  #[serde(rename = "comparisonAvailable")]
  pub comparison_available: bool,
  #[serde(rename = "proofTarget")]
  pub proof_target: String,
  #[serde(rename = "candidateArtifact")]
  pub candidate_artifact: String,
  #[serde(rename = "targetFingerprint")]
  pub target_fingerprint: Option<ArtifactFingerprint>,
  #[serde(rename = "candidateFingerprint")]
  pub candidate_fingerprint: Option<ArtifactFingerprint>,
  #[serde(rename = "firstMismatchOffset")]
  pub first_mismatch_offset: Option<u64>,
  #[serde(rename = "sectionInventoryEqual")]
  pub section_inventory_equal: Option<bool>,
  #[serde(rename = "symbolInventoryEqual")]
  pub symbol_inventory_equal: Option<bool>,
  #[serde(rename = "relocationInventoryEqual")]
  pub relocation_inventory_equal: Option<bool>,
  #[serde(rename = "blockingRows")]
  pub blocking_rows: Vec<ByteEquivalenceBlockingRow>,
  pub evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ByteEquivalenceBlockingRow {
  pub name: String,
  pub status: String,
  pub artifact: String,
  #[serde(rename = "failureClass")]
  pub failure_class: Option<FailureClass>,
  pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactComparison {
  pub target: ArtifactFingerprint,
  pub candidate: ArtifactFingerprint,
  #[serde(rename = "byteEqual")]
  pub byte_equal: bool,
  #[serde(rename = "firstMismatchOffset")]
  pub first_mismatch_offset: Option<u64>,
  #[serde(rename = "sectionInventoryEqual")]
  pub section_inventory_equal: bool,
  #[serde(rename = "symbolInventoryEqual")]
  pub symbol_inventory_equal: bool,
  #[serde(rename = "relocationInventoryEqual")]
  pub relocation_inventory_equal: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactFingerprint {
  pub path: String,
  #[serde(rename = "fileKind")]
  pub file_kind: String,
  #[serde(rename = "sizeBytes")]
  pub size_bytes: u64,
  pub sha256: String,
  pub sections: Vec<ComparableSection>,
  pub symbols: Vec<ComparableSymbol>,
  pub relocations: Vec<ComparableRelocation>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ComparableSection {
  pub name: String,
  pub size: u64,
  pub kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ComparableSymbol {
  pub name: String,
  pub size: u64,
  pub kind: String,
  pub scope: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ComparableRelocation {
  pub section: String,
  pub offset: u64,
  pub size: u8,
  pub kind: String,
  pub encoding: String,
  pub target: String,
  pub addend: i64,
}

impl VerificationRecord {
  pub fn new(case_id: &str) -> Self {
    let checks = vec![
      VerificationCheck::skipped("object_match", false, "Object proof not attempted yet."),
      VerificationCheck::skipped("binary_diff", true, "Binary rebuild output unavailable."),
      VerificationCheck::skipped(
        "section_comparison",
        true,
        "Section comparison requires target and candidate artifacts.",
      ),
      VerificationCheck::skipped(
        "symbol_comparison",
        true,
        "Symbol comparison requires target and candidate artifacts.",
      ),
      VerificationCheck::skipped(
        "relocation_comparison",
        true,
        "Relocation comparison requires target and candidate artifacts.",
      ),
      VerificationCheck::skipped("cfg_comparison", true, "CFG comparison not yet implemented."),
      VerificationCheck::skipped(
        "symbol_type_comparison",
        true,
        "Symbol and type comparison is evidence-only in this slice.",
      ),
      VerificationCheck::skipped(
        "type_relation_inventory",
        true,
        "Symbol/type relationship graph has not been recorded yet.",
      ),
      VerificationCheck::skipped(
        "source_artifact_audit",
        true,
        "Source artifact policy audit has not run yet.",
      ),
      VerificationCheck::skipped(
        "compiler_invocation_contract",
        true,
        "Exact compiler invocation ledger has not been evaluated yet.",
      ),
      VerificationCheck::skipped(
        "binary_fingerprint",
        true,
        "Input fingerprint not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "section_inventory",
        true,
        "Section inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "symbol_inventory",
        true,
        "Symbol inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "function_boundary_inventory",
        true,
        "Function boundary inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "relocation_inventory",
        true,
        "Relocation inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "dependency_inventory",
        true,
        "Dependency/import inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "debug_inventory",
        true,
        "Debug metadata inventory not recorded by verifier yet.",
      ),
      VerificationCheck::skipped(
        "toolchain_fingerprint",
        true,
        "Toolchain evidence not recorded by verifier yet.",
      ),
      VerificationCheck::skipped("rebuild_proof", false, "Rebuild not attempted."),
    ];
    let match_score = MatchScore::from_checks(&checks);
    Self {
      schema_version: 1,
      generated_at: now_rfc3339(),
      case_id: case_id.to_string(),
      status: "blocked".to_string(),
      failure_classes: Vec::new(),
      checks,
      artifact_comparison: None,
      build_unit_proof_results: Vec::new(),
      match_score,
    }
  }

  pub fn add_failure(&mut self, failure: FailureClass) {
    if !self.failure_classes.contains(&failure) {
      self.failure_classes.push(failure);
    }
  }

  pub fn set_check(&mut self, check: VerificationCheck) {
    if let Some(existing) = self.checks.iter_mut().find(|entry| entry.name == check.name) {
      *existing = check;
    } else {
      self.checks.push(check);
    }
    self.refresh_match_score();
  }

  pub fn finalize(&mut self) {
    self.generated_at = now_rfc3339();
    self.status = if self.failure_classes.is_empty()
      && self
        .checks
        .iter()
        .any(|check| check.name == "object_match" && check.status == "passed")
    {
      "verified".to_string()
    } else if self.failure_classes.is_empty() {
      "pending".to_string()
    } else {
      "blocked".to_string()
    };
    self.refresh_match_score();
  }

  pub fn refresh_match_score(&mut self) {
    self.match_score = MatchScore::from_checks(&self.checks);
  }
}

impl MatchScore {
  fn from_checks(checks: &[VerificationCheck]) -> Self {
    let components = checks
      .iter()
      .filter_map(MatchScoreComponent::from_check)
      .collect::<Vec<_>>();
    let max_score = components.iter().map(|component| component.weight).sum::<f64>();
    let total_score = components.iter().map(|component| component.score).sum::<f64>();
    let normalized = if max_score > 0.0 {
      total_score / max_score
    } else {
      0.0
    };
    let status = if components.is_empty() {
      "not_run"
    } else if components
      .iter()
      .any(|component| !component.advisory && component.status == "failed")
    {
      "mismatch"
    } else if components
      .iter()
      .any(|component| !component.advisory && component.status == "passed")
    {
      "partial"
    } else {
      "evidence_only"
    }
    .to_string();

    Self {
      total_score,
      max_score,
      normalized,
      status,
      components,
    }
  }
}

impl MatchScoreComponent {
  fn from_check(check: &VerificationCheck) -> Option<Self> {
    let weight = match check.name.as_str() {
      "object_match" => 50.0,
      "binary_diff" => 25.0,
      "rebuild_proof" => 10.0,
      "section_comparison" => 5.0,
      "symbol_comparison" => 5.0,
      "relocation_comparison" => 5.0,
      "cfg_comparison" => 3.0,
      "symbol_type_comparison" => 3.0,
      "cfg_inventory" => 1.0,
      "type_relation_inventory" => 1.0,
      "source_artifact_audit" => 3.0,
      "compiler_invocation_contract" => 1.0,
      "binary_fingerprint" => 1.0,
      "section_inventory" => 1.0,
      "symbol_inventory" => 1.0,
      "function_boundary_inventory" => 1.0,
      "relocation_inventory" => 1.0,
      "dependency_inventory" => 1.0,
      "debug_inventory" => 1.0,
      "toolchain_fingerprint" => 1.0,
      _ => return None,
    };
    let score = if check.status == "passed" { weight } else { 0.0 };
    Some(Self {
      name: check.name.clone(),
      weight,
      score,
      status: check.status.clone(),
      advisory: check.advisory,
      detail: check.detail.clone(),
    })
  }
}

impl VerificationCheck {
  pub fn passed(name: &str, advisory: bool, detail: impl Into<String>) -> Self {
    Self {
      name: name.to_string(),
      status: "passed".to_string(),
      advisory,
      detail: detail.into(),
    }
  }

  pub fn failed(name: &str, advisory: bool, detail: impl Into<String>) -> Self {
    Self {
      name: name.to_string(),
      status: "failed".to_string(),
      advisory,
      detail: detail.into(),
    }
  }

  pub fn skipped(name: &str, advisory: bool, detail: impl Into<String>) -> Self {
    Self {
      name: name.to_string(),
      status: "skipped".to_string(),
      advisory,
      detail: detail.into(),
    }
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UncertaintyLedger {
  #[serde(rename = "schemaVersion")]
  pub schema_version: u32,
  #[serde(rename = "generatedAt")]
  pub generated_at: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub items: Vec<UncertaintyItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UncertaintyItem {
  pub id: String,
  #[serde(rename = "failureClass")]
  pub failure_class: FailureClass,
  pub summary: String,
  pub blocking: bool,
  pub evidence: Vec<String>,
}

impl UncertaintyItem {
  pub fn blocking(
    id: &str,
    failure_class: FailureClass,
    summary: impl Into<String>,
    evidence: Vec<String>,
  ) -> Self {
    Self {
      id: id.to_string(),
      failure_class,
      summary: summary.into(),
      blocking: true,
      evidence,
    }
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectSnapshot {
  pub actions: ActionSummary,
  pub case: CaseManifest,
  pub analysis: AnalysisRecord,
  pub reconstruction: ReconstructionGraph,
  #[serde(rename = "cfgEvidence")]
  pub cfg_evidence: CfgEvidenceGraph,
  #[serde(rename = "typeRelations")]
  pub type_relations: TypeRelationGraph,
  #[serde(rename = "dependencyGraph")]
  pub dependency_graph: DependencyGraph,
  #[serde(rename = "buildPlan")]
  pub build_plan: BuildPlan,
  #[serde(rename = "compilerInvocation")]
  pub compiler_invocation: CompilerInvocationLedger,
  #[serde(rename = "sourceAudit")]
  pub source_audit: SourceAuditRecord,
  pub verification: VerificationRecord,
  pub uncertainty: UncertaintyLedger,
}

impl ProjectSnapshot {
  pub fn status(&self, project: &Path) -> ProjectStatus {
    let attempt_plan = derive_attempt_plan(self);
    let blocker_domains = derive_blocker_domains(&self.verification.failure_classes);
    let proof_targets = derive_proof_target_ledger(self);
    let build_unit_verification = derive_build_unit_verification_ledger(self);
    let verification_matrix = derive_verification_matrix(self);
    let source_verification = derive_source_verification_ledger(self);
    let roundtrip = derive_roundtrip_proof(self);
    let byte_equivalence = derive_byte_equivalence_ledger(self);
    let compiler_compatibility = derive_compiler_compatibility_ledger(self);
    let drift_analysis = derive_drift_analysis(self);
    let missing_analysis_providers = self
      .analysis
      .tool_availability
      .analysis_providers
      .iter()
      .filter(|provider| !provider.available)
      .map(|provider| format!("{}:{}({})", provider.role, provider.id, provider.kind))
      .collect::<Vec<_>>();
    ProjectStatus {
      project: project.display().to_string(),
      case_id: self.case.case_id.clone(),
      adapter: self.case.adapter.id.clone(),
      reconstruction_state: self.reconstruction.state.clone(),
      verification_status: self.verification.status.clone(),
      verification_matrix_status: verification_matrix.status.clone(),
      authoritative_proof_passed_count: verification_matrix.summary.authoritative_passed,
      authoritative_proof_total_count: verification_matrix.summary.authoritative_total,
      roundtrip_status: roundtrip.status,
      roundtrip_byte_equivalent: roundtrip.byte_equivalent,
      byte_equivalence_status: byte_equivalence.status,
      byte_equivalence_comparison_available: byte_equivalence.comparison_available,
      drift_analysis_status: drift_analysis.status,
      blocking_drift_count: drift_analysis.blocking_drift_count,
      failure_classes: self
        .verification
        .failure_classes
        .iter()
        .map(ToString::to_string)
        .collect(),
      blocker_domains,
      uncertainty_count: self.uncertainty.items.len(),
      translation_unit_count: self.reconstruction.project_structure.translation_units.len(),
      function_count: self.analysis.target.functions.len(),
      cfg_status: self.cfg_evidence.status.clone(),
      cfg_unresolved_function_count: self.cfg_evidence.unresolved_function_count,
      type_candidate_count: self.type_relations.type_candidate_count,
      unresolved_type_count: self.type_relations.unresolved_type_count,
      dependency_import_count: self.dependency_graph.import_count,
      dependency_export_count: self.dependency_graph.export_count,
      unresolved_dependency_count: self.dependency_graph.unresolved_dependency_count,
      build_unit_count: self.build_plan.build_units.len(),
      proof_target_status: proof_targets.status,
      mapped_proof_target_unit_count: proof_targets.mapped_unit_count,
      build_unit_verification_status: build_unit_verification.status,
      proof_attributed_build_unit_count: build_unit_verification.proof_attributed_count,
      verified_build_unit_count: build_unit_verification.verified_unit_count,
      byte_proved_build_unit_count: build_unit_verification.byte_proved_unit_count,
      compiler_invocation_status: self.compiler_invocation.status.clone(),
      compiler_invocation_candidate_count: self.compiler_invocation.candidate_count,
      recovered_compiler_invocation_count: self.compiler_invocation.recovered_invocation_count,
      compiler_compatibility_status: compiler_compatibility.status,
      compiler_compatibility_profile_count: compiler_compatibility.profile_count,
      compiler_compatibility_public_source_modeled_count: compiler_compatibility
        .public_source_modeled_count,
      compiler_compatibility_proprietary_gap_count: compiler_compatibility.proprietary_gap_count,
      source_audit_status: self.source_audit.status.clone(),
      source_verification_status: source_verification.status.clone(),
      source_artifact_count: self.source_audit.artifact_count,
      blocked_source_artifact_count: self.source_audit.blocked_stub_count,
      suspicious_source_artifact_count: self.source_audit.suspicious_count,
      verified_source_count: source_verification.verified_source_count,
      byte_proved_candidate_count: source_verification.byte_proved_candidate_count,
      ranking_status: self.build_plan.toolchain.ranking_status.clone(),
      recommended_profile: self.build_plan.toolchain.recommended_profile.clone(),
      selected_profile: self.build_plan.toolchain.selected_profile.clone(),
      analysis_provider_count: self.analysis.tool_availability.analysis_providers.len(),
      available_analysis_provider_count: self
        .analysis
        .tool_availability
        .analysis_providers
        .iter()
        .filter(|provider| provider.available)
        .count(),
      missing_analysis_providers,
      actionable_attempt_count: attempt_plan.actionable_attempt_count,
      top_attempts: attempt_plan.top_attempts,
      next_actions: attempt_plan.next_actions,
      artifacts: vec![
        "case.yaml".to_string(),
        "analysis.json".to_string(),
        "reconstruction.json".to_string(),
        "cfg-evidence.json".to_string(),
        "type-relations.json".to_string(),
        "dependency-graph.json".to_string(),
        "build-plan.json".to_string(),
        "proof-targets.json".to_string(),
        "compiler-invocation.json".to_string(),
        "source-audit.json".to_string(),
        "build-unit-verification.json".to_string(),
        "source-verification.json".to_string(),
        "build-graph.json".to_string(),
        "build-manifest.json".to_string(),
        "toolchain-manifest.json".to_string(),
        "attempt-matrix.json".to_string(),
        "upstream-evidence.json".to_string(),
        "compiler-compatibility.json".to_string(),
        "verification.json".to_string(),
        "verification-matrix.json".to_string(),
        "roundtrip.json".to_string(),
        "byte-equivalence.json".to_string(),
        "drift-analysis.json".to_string(),
        "uncertainty.json".to_string(),
        "objdiff.json".to_string(),
        "report.md".to_string(),
      ],
    }
  }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectStatus {
  pub project: String,
  #[serde(rename = "caseId")]
  pub case_id: String,
  pub adapter: String,
  #[serde(rename = "reconstructionState")]
  pub reconstruction_state: String,
  #[serde(rename = "verificationStatus")]
  pub verification_status: String,
  #[serde(rename = "verificationMatrixStatus")]
  pub verification_matrix_status: String,
  #[serde(rename = "authoritativeProofPassedCount")]
  pub authoritative_proof_passed_count: usize,
  #[serde(rename = "authoritativeProofTotalCount")]
  pub authoritative_proof_total_count: usize,
  #[serde(rename = "roundtripStatus")]
  pub roundtrip_status: String,
  #[serde(rename = "roundtripByteEquivalent")]
  pub roundtrip_byte_equivalent: bool,
  #[serde(rename = "byteEquivalenceStatus")]
  pub byte_equivalence_status: String,
  #[serde(rename = "byteEquivalenceComparisonAvailable")]
  pub byte_equivalence_comparison_available: bool,
  #[serde(rename = "driftAnalysisStatus")]
  pub drift_analysis_status: String,
  #[serde(rename = "blockingDriftCount")]
  pub blocking_drift_count: usize,
  #[serde(rename = "failureClasses")]
  pub failure_classes: Vec<String>,
  #[serde(rename = "blockerDomains")]
  pub blocker_domains: Vec<String>,
  #[serde(rename = "uncertaintyCount")]
  pub uncertainty_count: usize,
  #[serde(rename = "translationUnitCount")]
  pub translation_unit_count: usize,
  #[serde(rename = "functionCount")]
  pub function_count: usize,
  #[serde(rename = "cfgStatus")]
  pub cfg_status: String,
  #[serde(rename = "cfgUnresolvedFunctionCount")]
  pub cfg_unresolved_function_count: usize,
  #[serde(rename = "typeCandidateCount")]
  pub type_candidate_count: usize,
  #[serde(rename = "unresolvedTypeCount")]
  pub unresolved_type_count: usize,
  #[serde(rename = "dependencyImportCount")]
  pub dependency_import_count: usize,
  #[serde(rename = "dependencyExportCount")]
  pub dependency_export_count: usize,
  #[serde(rename = "unresolvedDependencyCount")]
  pub unresolved_dependency_count: usize,
  #[serde(rename = "buildUnitCount")]
  pub build_unit_count: usize,
  #[serde(rename = "proofTargetStatus")]
  pub proof_target_status: String,
  #[serde(rename = "mappedProofTargetUnitCount")]
  pub mapped_proof_target_unit_count: usize,
  #[serde(rename = "buildUnitVerificationStatus")]
  pub build_unit_verification_status: String,
  #[serde(rename = "proofAttributedBuildUnitCount")]
  pub proof_attributed_build_unit_count: usize,
  #[serde(rename = "verifiedBuildUnitCount")]
  pub verified_build_unit_count: usize,
  #[serde(rename = "byteProvedBuildUnitCount")]
  pub byte_proved_build_unit_count: usize,
  #[serde(rename = "compilerInvocationStatus")]
  pub compiler_invocation_status: String,
  #[serde(rename = "compilerInvocationCandidateCount")]
  pub compiler_invocation_candidate_count: usize,
  #[serde(rename = "recoveredCompilerInvocationCount")]
  pub recovered_compiler_invocation_count: usize,
  #[serde(rename = "compilerCompatibilityStatus")]
  pub compiler_compatibility_status: String,
  #[serde(rename = "compilerCompatibilityProfileCount")]
  pub compiler_compatibility_profile_count: usize,
  #[serde(rename = "compilerCompatibilityPublicSourceModeledCount")]
  pub compiler_compatibility_public_source_modeled_count: usize,
  #[serde(rename = "compilerCompatibilityProprietaryGapCount")]
  pub compiler_compatibility_proprietary_gap_count: usize,
  #[serde(rename = "sourceAuditStatus")]
  pub source_audit_status: String,
  #[serde(rename = "sourceVerificationStatus")]
  pub source_verification_status: String,
  #[serde(rename = "sourceArtifactCount")]
  pub source_artifact_count: usize,
  #[serde(rename = "blockedSourceArtifactCount")]
  pub blocked_source_artifact_count: usize,
  #[serde(rename = "suspiciousSourceArtifactCount")]
  pub suspicious_source_artifact_count: usize,
  #[serde(rename = "verifiedSourceCount")]
  pub verified_source_count: usize,
  #[serde(rename = "byteProvedCandidateCount")]
  pub byte_proved_candidate_count: usize,
  #[serde(rename = "rankingStatus")]
  pub ranking_status: String,
  #[serde(rename = "recommendedProfile")]
  pub recommended_profile: Option<String>,
  #[serde(rename = "selectedProfile")]
  pub selected_profile: Option<String>,
  #[serde(rename = "analysisProviderCount")]
  pub analysis_provider_count: usize,
  #[serde(rename = "availableAnalysisProviderCount")]
  pub available_analysis_provider_count: usize,
  #[serde(rename = "missingAnalysisProviders")]
  pub missing_analysis_providers: Vec<String>,
  #[serde(rename = "actionableAttemptCount")]
  pub actionable_attempt_count: usize,
  #[serde(rename = "topAttempts")]
  pub top_attempts: Vec<StatusAttempt>,
  #[serde(rename = "nextActions")]
  pub next_actions: Vec<StatusNextAction>,
  pub artifacts: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StatusAttempt {
  pub id: String,
  #[serde(rename = "rowStatus")]
  pub row_status: String,
  pub priority: u32,
  #[serde(rename = "priorityClass")]
  pub priority_class: String,
  #[serde(rename = "nextAction")]
  pub next_action: String,
  #[serde(rename = "hostReady")]
  pub host_ready: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StatusNextAction {
  pub priority: u32,
  pub action: String,
}

pub fn derive_blocker_domains(failures: &[FailureClass]) -> Vec<String> {
  let mut domains = failures
    .iter()
    .map(FailureClass::blocker_domain)
    .collect::<Vec<_>>();
  domains.sort_by_key(|domain| match domain {
    BlockerDomain::Infra => 0,
    BlockerDomain::Proof => 1,
    BlockerDomain::Recovery => 2,
    BlockerDomain::Verification => 3,
  });
  domains.dedup();
  domains.into_iter().map(|domain| domain.to_string()).collect()
}

pub fn derive_verification_matrix(snapshot: &ProjectSnapshot) -> VerificationMatrix {
  let components = snapshot
    .verification
    .match_score
    .components
    .iter()
    .map(|component| (component.name.as_str(), component))
    .collect::<BTreeMap<_, _>>();

  let rows = snapshot
    .verification
    .checks
    .iter()
    .map(|check| {
      let component = components.get(check.name.as_str());
      let authority = verification_authority(&check.name, check.advisory).to_string();
      let blocking = verification_row_blocking(&check.name, &authority, &check.status);
      VerificationMatrixRow {
        name: check.name.clone(),
        domain: verification_domain(&check.name).to_string(),
        authority,
        status: check.status.clone(),
        weight: component.map(|component| component.weight).unwrap_or(0.0),
        score: component.map(|component| component.score).unwrap_or(0.0),
        blocking,
        artifact: verification_artifact(&check.name).to_string(),
        failure_class: verification_failure_class(&check.name, &check.status),
        detail: check.detail.clone(),
      }
    })
    .collect::<Vec<_>>();

  let authoritative_total = rows
    .iter()
    .filter(|row| row.authority == "authoritative")
    .count();
  let authoritative_passed = rows
    .iter()
    .filter(|row| row.authority == "authoritative" && row.status == "passed")
    .count();
  let authoritative_failed = rows
    .iter()
    .filter(|row| row.authority == "authoritative" && row.status == "failed")
    .count();
  let advisory_total = rows.iter().filter(|row| row.authority == "advisory").count();
  let advisory_passed = rows
    .iter()
    .filter(|row| row.authority == "advisory" && row.status == "passed")
    .count();
  let advisory_failed = rows
    .iter()
    .filter(|row| row.authority == "advisory" && row.status == "failed")
    .count();
  let policy_total = rows.iter().filter(|row| row.authority == "policy").count();
  let policy_passed = rows
    .iter()
    .filter(|row| row.authority == "policy" && row.status == "passed")
    .count();
  let policy_failed = rows
    .iter()
    .filter(|row| row.authority == "policy" && row.status == "failed")
    .count();
  let blocked_rows = rows.iter().filter(|row| row.blocking).count();
  let coverage_status = if authoritative_passed == authoritative_total && authoritative_total > 0 {
    "complete"
  } else if rows.iter().any(|row| row.status == "passed") {
    "partial"
  } else {
    "not_run"
  }
  .to_string();
  let status = if authoritative_total > 0
    && authoritative_passed == authoritative_total
    && blocked_rows == 0
  {
    "verified"
  } else if blocked_rows > 0 {
    "blocked"
  } else {
    "pending"
  }
  .to_string();

  VerificationMatrix {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    summary: VerificationMatrixSummary {
      authoritative_total,
      authoritative_passed,
      authoritative_failed,
      advisory_total,
      advisory_passed,
      advisory_failed,
      policy_total,
      policy_passed,
      policy_failed,
      blocked_rows,
      coverage_status,
      score_status: snapshot.verification.match_score.status.clone(),
      normalized_score: snapshot.verification.match_score.normalized,
      blocker_domains: derive_blocker_domains(&snapshot.verification.failure_classes),
    },
    rows,
  }
}

pub fn derive_proof_target_ledger(snapshot: &ProjectSnapshot) -> ProofTargetLedger {
  let collection_kind = if snapshot.build_plan.build_units.len() > 1 {
    "archive-members"
  } else {
    "single-object"
  }
  .to_string();
  let member_names = snapshot
    .analysis
    .target
    .archive_members
    .iter()
    .map(|member| (member.id.as_str(), member.name.as_str()))
    .collect::<BTreeMap<_, _>>();
  let units = snapshot
    .build_plan
    .build_units
    .iter()
    .map(|unit| {
      let status = if unit.proof_target_status.is_empty() {
        if unit.proof_target == "unavailable" {
          "unavailable".to_string()
        } else {
          "mapped".to_string()
        }
      } else if unit.proof_target_status == "configured" && unit.proof_target != "unavailable" {
        "mapped".to_string()
      } else {
        unit.proof_target_status.clone()
      };
      let kind = if snapshot.build_plan.build_units.len() > 1 {
        "archive-member".to_string()
      } else {
        "direct-object".to_string()
      };
      let mut blockers = match status.as_str() {
        "mapped" => Vec::new(),
        "proof-source-missing" => {
          vec!["Configured proof source path does not resolve to a local artifact.".to_string()]
        }
        "proof-source-unparsed" => {
          vec!["Configured proof source could not be parsed into per-unit proof artifacts.".to_string()]
        }
        "thin-member-unavailable" => {
          vec!["Matched proof archive member is thin and cannot be materialized without external member resolution.".to_string()]
        }
        "missing-member-match" => {
          vec!["No matching proof archive member was found for this build unit.".to_string()]
        }
        _ => vec!["Proof target is unavailable for this build unit.".to_string()],
      };
      blockers.extend(
        unit
          .blockers
          .iter()
          .filter(|blocker| blocker.to_ascii_lowercase().contains("proof"))
          .cloned(),
      );
      blockers.sort();
      blockers.dedup();
      let mut evidence = vec![
        format!("collection_kind={collection_kind}"),
        format!("proof_source_path={}", unit.proof_source_path),
        format!("proof_target={}", unit.proof_target),
        format!("proof_target_locator={}", unit.proof_target_locator),
      ];
      if let Some(index) = unit.proof_target_member_index {
        evidence.push(format!("proof_target_member_index={index}"));
      }
      if let Some(name) = member_names.get(unit.id.as_str()) {
        evidence.push(format!("member_name={name}"));
      }
      ProofTargetUnit {
        build_unit_id: unit.id.clone(),
        proof_source_path: unit.proof_source_path.clone(),
        proof_target: unit.proof_target.clone(),
        kind,
        status,
        locator: unit.proof_target_locator.clone(),
        member_index: unit.proof_target_member_index,
        member_name: member_names.get(unit.id.as_str()).map(|name| (*name).to_string()),
        blockers,
        evidence,
      }
    })
    .collect::<Vec<_>>();
  let unit_count = units.len();
  let mapped_unit_count = units.iter().filter(|unit| unit.status == "mapped").count();
  let unavailable_unit_count = unit_count.saturating_sub(mapped_unit_count);
  let status = if unit_count > 0 && mapped_unit_count == unit_count {
    "mapped"
  } else if mapped_unit_count > 0 {
    "partial"
  } else {
    "blocked"
  }
  .to_string();
  ProofTargetLedger {
    schema_version: 1,
    generated_at: snapshot.build_plan.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    source_path: snapshot.case.proof.target_object_path.clone(),
    comparator: snapshot.case.proof.comparator.clone(),
    collection_kind,
    unit_count,
    mapped_unit_count,
    unavailable_unit_count,
    units,
  }
}

pub fn derive_build_unit_verification_ledger(
  snapshot: &ProjectSnapshot,
) -> BuildUnitVerificationLedger {
  let proof_targets = derive_proof_target_ledger(snapshot);
  let proof_targets_by_unit = proof_targets
    .units
    .iter()
    .map(|unit| (unit.build_unit_id.as_str(), unit))
    .collect::<BTreeMap<_, _>>();
  let invocations_by_unit = snapshot
    .compiler_invocation
    .invocations
    .iter()
    .fold(BTreeMap::<&str, Vec<&CompilerInvocationCandidate>>::new(), |mut acc, invocation| {
      acc.entry(invocation.build_unit_id.as_str())
        .or_default()
        .push(invocation);
      acc
    });
  let check_status = |name: &str| {
    snapshot
      .verification
      .checks
      .iter()
      .find(|check| check.name == name)
      .map(|check| check.status.as_str())
      .unwrap_or("skipped")
      .to_string()
  };
  let global_rebuild_status = check_status("rebuild_proof");
  let global_object_match_status = check_status("object_match");
  let global_binary_diff_status = check_status("binary_diff");
  let proof_results_by_unit = snapshot
    .verification
    .build_unit_proof_results
    .iter()
    .map(|result| (result.build_unit_id.as_str(), result))
    .collect::<BTreeMap<_, _>>();

  let units = snapshot
    .build_plan
    .build_units
    .iter()
    .map(|unit| {
      let invocations = invocations_by_unit
        .get(unit.id.as_str())
        .cloned()
        .unwrap_or_default();
      let proof_target = proof_targets_by_unit.get(unit.id.as_str()).copied();
      let proof_result = proof_results_by_unit.get(unit.id.as_str()).copied();
      let exact_invocation_recovered = invocations
        .iter()
        .any(|invocation| invocation.exact_command_recovered);
      let proof_target_available = proof_target
        .map(|target| target.status == "mapped")
        .unwrap_or(unit.proof_target != "unavailable");
      let rebuild_status = proof_result
        .map(|result| result.rebuild_status.as_str())
        .unwrap_or(global_rebuild_status.as_str())
        .to_string();
      let object_match_status = proof_result
        .map(|result| result.object_match_status.as_str())
        .unwrap_or(global_object_match_status.as_str())
        .to_string();
      let binary_diff_status = proof_result
        .map(|result| result.binary_diff_status.as_str())
        .unwrap_or(global_binary_diff_status.as_str())
        .to_string();
      let proof_attributed = proof_target_available
        && (proof_result.is_some()
          || (unit.object_path == snapshot.build_plan.candidate_object
            && unit.source_path == snapshot.build_plan.candidate_source
            && unit.proof_target == snapshot.build_plan.proof_target));
      let byte_equivalent_proof = proof_attributed
        && rebuild_status == "passed"
        && object_match_status == "passed"
        && binary_diff_status == "passed";

      let mut blockers = Vec::new();
      let status = if !proof_target_available {
        blockers.push("per-build-unit proof target is unavailable".to_string());
        blockers.push("current proof cannot be attributed to this build unit".to_string());
        "proof-unavailable"
      } else if byte_equivalent_proof && exact_invocation_recovered {
        "verified-build-unit"
      } else if byte_equivalent_proof {
        blockers.push(
          "build unit has object/byte proof, but exact compiler invocation remains unresolved"
            .to_string(),
        );
        "byte-proved-build-unit"
      } else {
        if !proof_attributed {
          blockers.push("current proof rows are not attributable to this build unit yet".to_string());
        }
        if rebuild_status != "passed" {
          blockers.push(format!("rebuild proof status is {}", rebuild_status));
        }
        if object_match_status != "passed" {
          blockers.push(format!("object match status is {}", object_match_status));
        }
        if binary_diff_status != "passed" {
          blockers.push(format!("binary diff status is {}", binary_diff_status));
        }
        if !exact_invocation_recovered {
          blockers.push("exact compiler invocation has not been recovered for this build unit".to_string());
        }
        "unverified-build-unit"
      }
      .to_string();

      let mut evidence = vec![
        format!("proof_target_available={proof_target_available}"),
        format!("proof_attributed={proof_attributed}"),
        format!("rebuild_status={rebuild_status}"),
        format!("object_match_status={object_match_status}"),
        format!("binary_diff_status={binary_diff_status}"),
        format!(
          "compiler_invocation_status={}",
          snapshot.compiler_invocation.status
        ),
        format!("exact_invocation_recovered={exact_invocation_recovered}"),
        format!("proof_target_status={}", unit.proof_target_status),
        format!("proof_target_locator={}", unit.proof_target_locator),
        format!("proof_source_path={}", unit.proof_source_path),
      ];
      if let Some(proof_result) = proof_result {
        evidence.push(format!("rebuild_detail={}", proof_result.rebuild_detail));
        evidence.push(format!("object_match_detail={}", proof_result.object_match_detail));
        evidence.push(format!("binary_diff_detail={}", proof_result.binary_diff_detail));
        if let Some(comparison) = proof_result.artifact_comparison.as_ref() {
          evidence.push(format!("comparison_byte_equal={}", comparison.byte_equal));
          evidence.push(format!(
            "comparison_section_inventory_equal={}",
            comparison.section_inventory_equal
          ));
          evidence.push(format!(
            "comparison_symbol_inventory_equal={}",
            comparison.symbol_inventory_equal
          ));
          evidence.push(format!(
            "comparison_relocation_inventory_equal={}",
            comparison.relocation_inventory_equal
          ));
          evidence.push(format!(
            "comparison_first_mismatch_offset={}",
            comparison
              .first_mismatch_offset
              .map(|offset| offset.to_string())
              .unwrap_or_else(|| "none".to_string())
          ));
        } else {
          evidence.push("artifact_comparison=not-run".to_string());
        }
        evidence.extend(
          proof_result
            .evidence
            .iter()
            .cloned()
            .map(|item| format!("proof_result_evidence={item}")),
        );
      } else {
        evidence.push("per_build_unit_verification=not-run".to_string());
      }
      if let Some(proof_target) = proof_target {
        evidence.extend(
          proof_target
            .evidence
            .iter()
            .cloned()
            .map(|item| format!("proof_target_evidence={item}")),
        );
      }
      if invocations.is_empty() {
        evidence.push("invocation_candidates=0".to_string());
      } else {
        evidence.push(format!("invocation_candidates={}", invocations.len()));
        evidence.extend(
          invocations
            .iter()
            .map(|invocation| format!("invocation_candidate={}:{}", invocation.id, invocation.status)),
        );
      }
      evidence.extend(unit.blockers.iter().map(|blocker| format!("build_unit_blocker={blocker}")));

      BuildUnitVerificationUnit {
        id: unit.id.clone(),
        source_path: unit.source_path.clone(),
        object_path: unit.object_path.clone(),
        proof_target: unit.proof_target.clone(),
        status,
        proof_attributed,
        rebuild_status: rebuild_status.clone(),
        object_match_status: object_match_status.clone(),
        binary_diff_status: binary_diff_status.clone(),
        compiler_invocation_status: snapshot.compiler_invocation.status.clone(),
        exact_invocation_recovered,
        blockers,
        evidence,
      }
    })
    .collect::<Vec<_>>();

  let unit_count = units.len();
  let proof_attributed_count = units.iter().filter(|unit| unit.proof_attributed).count();
  let verified_unit_count = units
    .iter()
    .filter(|unit| unit.status == "verified-build-unit")
    .count();
  let byte_proved_unit_count = units
    .iter()
    .filter(|unit| unit.status == "byte-proved-build-unit")
    .count();
  let unverified_unit_count = units
    .iter()
    .filter(|unit| unit.status == "unverified-build-unit")
    .count();
  let proof_unavailable_count = units
    .iter()
    .filter(|unit| unit.status == "proof-unavailable")
    .count();
  let status = if unit_count > 0 && verified_unit_count == unit_count {
    "verified"
  } else if verified_unit_count > 0 || byte_proved_unit_count > 0 {
    "partial"
  } else if proof_unavailable_count == unit_count && unit_count > 0 {
    "blocked"
  } else {
    "unverified"
  }
  .to_string();

  BuildUnitVerificationLedger {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    unit_count,
    proof_attributed_count,
    verified_unit_count,
    byte_proved_unit_count,
    unverified_unit_count,
    proof_unavailable_count,
    units,
  }
}

pub fn derive_source_verification_ledger(snapshot: &ProjectSnapshot) -> SourceVerificationLedger {
  let build_unit_verification = derive_build_unit_verification_ledger(snapshot);
  let build_units = snapshot
    .build_plan
    .build_units
    .iter()
    .map(|unit| (unit.source_path.as_str(), unit))
    .collect::<BTreeMap<_, _>>();
  let build_unit_verification_by_source = build_unit_verification
    .units
    .iter()
    .map(|unit| (unit.source_path.as_str(), unit))
    .collect::<BTreeMap<_, _>>();
  let audit_artifacts = snapshot
    .source_audit
    .artifacts
    .iter()
    .map(|artifact| (artifact.path.as_str(), artifact))
    .collect::<BTreeMap<_, _>>();

  let artifacts = snapshot
    .reconstruction
    .source_candidates
    .iter()
    .map(|candidate| {
      let audit = audit_artifacts.get(candidate.path.as_str());
      let build_unit = build_units.get(candidate.path.as_str()).copied();
      let build_unit_verification = build_unit_verification_by_source.get(candidate.path.as_str());
      let rebuild_status = build_unit_verification
        .map(|unit| unit.rebuild_status.as_str())
        .unwrap_or("skipped")
        .to_string();
      let object_match_status = build_unit_verification
        .map(|unit| unit.object_match_status.as_str())
        .unwrap_or("skipped")
        .to_string();
      let binary_diff_status = build_unit_verification
        .map(|unit| unit.binary_diff_status.as_str())
        .unwrap_or("skipped")
        .to_string();
      let exact_invocation_recovered = build_unit_verification
        .map(|unit| unit.exact_invocation_recovered)
        .unwrap_or(false);
      let proof_attributed = build_unit_verification
        .map(|unit| unit.proof_attributed)
        .unwrap_or(false);
      let byte_equivalent_proof = build_unit_verification
        .map(|unit| {
          matches!(
            unit.status.as_str(),
            "byte-proved-build-unit" | "verified-build-unit"
          )
        })
        .unwrap_or(false);

      let audit_verdict = audit
        .map(|artifact| artifact.verdict.as_str())
        .unwrap_or("audit-missing")
        .to_string();
      let mut blockers = Vec::new();
      let status = match audit_verdict.as_str() {
        "policy-violation" => {
          blockers.push("source artifact violates source-output policy".to_string());
          "policy-violation"
        }
        "marked-blocking-stub" => {
          blockers.push(
            "source artifact is an explicit blocking scaffold, not recovered logic".to_string(),
          );
          "marked-blocking-stub"
        }
        _ if byte_equivalent_proof && exact_invocation_recovered => "verified-recovered-source",
        _ if byte_equivalent_proof => {
          blockers.push(
            "byte/object proof passed for this candidate, but exact compiler invocation remains unresolved"
              .to_string(),
          );
          "byte-proved-candidate"
        }
        _ => {
          if !proof_attributed {
            blockers.push(
              "current proof rows are not attributable to this source candidate yet".to_string(),
            );
          }
          if rebuild_status != "passed" {
            blockers.push(format!("rebuild proof status is {}", rebuild_status));
          }
          if object_match_status != "passed" {
            blockers.push(format!("object match status is {}", object_match_status));
          }
          if binary_diff_status != "passed" {
            blockers.push(format!("binary diff status is {}", binary_diff_status));
          }
          if !exact_invocation_recovered {
            blockers.push("exact compiler invocation has not been recovered".to_string());
          }
          "unverified-source"
        }
      }
      .to_string();

      let mut evidence = vec![
        format!("audit_verdict={audit_verdict}"),
        format!("proof_attributed={proof_attributed}"),
        format!(
          "rebuild_status={}",
          build_unit_verification
            .map(|unit| unit.rebuild_status.as_str())
            .unwrap_or("skipped")
        ),
        format!(
          "object_match_status={}",
          build_unit_verification
            .map(|unit| unit.object_match_status.as_str())
            .unwrap_or("skipped")
        ),
        format!(
          "binary_diff_status={}",
          build_unit_verification
            .map(|unit| unit.binary_diff_status.as_str())
            .unwrap_or("skipped")
        ),
        format!(
          "compiler_invocation_status={}",
          snapshot.compiler_invocation.status
        ),
        format!("exact_invocation_recovered={exact_invocation_recovered}"),
      ];
      if let Some(unit) = build_unit {
        evidence.push(format!("build_unit_id={}", unit.id));
        evidence.push(format!("build_unit_status={}", unit.status));
      }
      if let Some(unit) = build_unit_verification {
        evidence.extend(
          unit.evidence
            .iter()
            .map(|item| format!("build_unit_verification={item}")),
        );
      }
      if let Some(artifact) = audit {
        evidence.extend(artifact.evidence.iter().cloned());
      }

      SourceVerificationArtifact {
        path: candidate.path.clone(),
        language: candidate.language.clone(),
        kind: candidate.kind.clone(),
        status,
        audit_verdict,
        source_candidate_status: candidate.status.clone(),
        build_unit_id: build_unit.map(|unit| unit.id.clone()),
        proof_attributed,
        rebuild_status: build_unit_verification
          .map(|unit| unit.rebuild_status.clone())
          .unwrap_or_else(|| "skipped".to_string()),
        object_match_status: build_unit_verification
          .map(|unit| unit.object_match_status.clone())
          .unwrap_or_else(|| "skipped".to_string()),
        binary_diff_status: build_unit_verification
          .map(|unit| unit.binary_diff_status.clone())
          .unwrap_or_else(|| "skipped".to_string()),
        compiler_invocation_status: snapshot.compiler_invocation.status.clone(),
        exact_invocation_recovered,
        byte_equivalent_proof,
        verified_recovered_source: byte_equivalent_proof && exact_invocation_recovered,
        blockers,
        evidence,
      }
    })
    .collect::<Vec<_>>();

  let artifact_count = artifacts.len();
  let verified_source_count = artifacts
    .iter()
    .filter(|artifact| artifact.status == "verified-recovered-source")
    .count();
  let byte_proved_candidate_count = artifacts
    .iter()
    .filter(|artifact| artifact.status == "byte-proved-candidate")
    .count();
  let unverified_source_count = artifacts
    .iter()
    .filter(|artifact| artifact.status == "unverified-source")
    .count();
  let blocked_stub_count = artifacts
    .iter()
    .filter(|artifact| artifact.status == "marked-blocking-stub")
    .count();
  let policy_violation_count = artifacts
    .iter()
    .filter(|artifact| artifact.status == "policy-violation")
    .count();
  let exact_invocation_recovered_count = artifacts
    .iter()
    .filter(|artifact| artifact.exact_invocation_recovered)
    .count();
  let proof_attributed_count = artifacts
    .iter()
    .filter(|artifact| artifact.proof_attributed)
    .count();
  let status = if artifact_count > 0 && verified_source_count == artifact_count {
    "verified"
  } else if policy_violation_count > 0 {
    "failed"
  } else if verified_source_count > 0 || byte_proved_candidate_count > 0 {
    "partial"
  } else if blocked_stub_count == artifact_count && artifact_count > 0 {
    "blocked"
  } else {
    "unverified"
  }
  .to_string();

  SourceVerificationLedger {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    artifact_count,
    verified_source_count,
    byte_proved_candidate_count,
    unverified_source_count,
    blocked_stub_count,
    policy_violation_count,
    exact_invocation_recovered_count,
    proof_attributed_count,
    artifacts,
  }
}

pub fn derive_roundtrip_proof(snapshot: &ProjectSnapshot) -> RoundTripProof {
  let check = |name: &str| {
    snapshot
      .verification
      .checks
      .iter()
      .find(|check| check.name == name)
  };
  let source_verification = derive_source_verification_ledger(snapshot);
  let source_ready = snapshot.source_audit.status == "passed"
    && source_verification.blocked_stub_count == 0
    && source_verification.policy_violation_count == 0;
  let source_recovery_ready = source_ready
    && source_verification.unverified_source_count == 0
    && source_verification.byte_proved_candidate_count == 0
    && source_verification.verified_source_count > 0;
  let exact_invocation_ready = snapshot.compiler_invocation.recovered_invocation_count > 0;
  let rebuild_status = check("rebuild_proof")
    .map(|check| check.status.as_str())
    .unwrap_or("skipped");
  let object_status = check("object_match")
    .map(|check| check.status.as_str())
    .unwrap_or("skipped");
  let binary_status = check("binary_diff")
    .map(|check| check.status.as_str())
    .unwrap_or("skipped");
  let matrix = derive_verification_matrix(snapshot);

  let mut stages = Vec::new();
  stages.push(roundtrip_stage(
    "source_recovery",
    if source_recovery_ready { "passed" } else { "blocked" },
    false,
    "source-audit.json",
    if source_recovery_ready {
      "Generated source artifacts passed policy and are marked as verified recovered source.".to_string()
    } else {
      format!(
        "{} verified source candidate(s), {} byte-proved candidate(s), {} unverified candidate(s), {} blocking stub(s), and {} policy violation(s).",
        source_verification.verified_source_count,
        source_verification.byte_proved_candidate_count,
        source_verification.unverified_source_count,
        source_verification.blocked_stub_count,
        source_verification.policy_violation_count
      )
    },
    if source_recovery_ready {
      Vec::new()
    } else {
      [
        (source_verification.verified_source_count == 0)
          .then(|| "verified recovered source is unavailable".to_string()),
        (source_verification.byte_proved_candidate_count > 0)
          .then(|| "candidate source matches current proof targets but exact compiler invocation is unresolved".to_string()),
        (source_verification.unverified_source_count > 0)
          .then(|| "candidate source exists but is not marked as verified recovered source".to_string()),
        (source_verification.blocked_stub_count > 0)
          .then(|| "blocking stubs are not final recovered source".to_string()),
        (source_verification.policy_violation_count > 0)
          .then(|| "source policy violations require review".to_string()),
      ]
      .into_iter()
      .flatten()
      .collect()
    },
  ));
  stages.push(roundtrip_stage(
    "compiler_invocation",
    if exact_invocation_ready { "passed" } else { "blocked" },
    false,
    "compiler-invocation.json",
    if exact_invocation_ready {
      format!(
        "{} exact compiler invocation(s) recovered.",
        snapshot.compiler_invocation.recovered_invocation_count
      )
    } else {
      "No exact compiler/linker invocation has been recovered.".to_string()
    },
    if exact_invocation_ready {
      Vec::new()
    } else {
      snapshot.compiler_invocation.missing_evidence.clone()
    },
  ));
  stages.push(roundtrip_stage(
    "rebuild",
    rebuild_status,
    true,
    "build-plan.json",
    check_detail(check("rebuild_proof")),
    check_blockers(check("rebuild_proof")),
  ));
  stages.push(roundtrip_stage(
    "object_match",
    object_status,
    true,
    "objdiff.json",
    check_detail(check("object_match")),
    check_blockers(check("object_match")),
  ));
  stages.push(roundtrip_stage(
    "binary_diff",
    binary_status,
    true,
    "verification.json",
    check_detail(check("binary_diff")),
    check_blockers(check("binary_diff")),
  ));

  let proof_chain_complete = stages.iter().all(|stage| stage.status == "passed");
  let byte_equivalent = object_status == "passed" && binary_status == "passed";
  let mut blockers = stages
    .iter()
    .flat_map(|stage| {
      stage
        .blockers
        .iter()
        .map(|blocker| format!("{}: {}", stage.name, blocker))
    })
    .collect::<Vec<_>>();
  blockers.extend(
    matrix
      .rows
      .iter()
      .filter(|row| row.blocking)
      .map(|row| format!("{}: {}", row.name, row.detail)),
  );
  blockers.sort();
  blockers.dedup();
  let status = if proof_chain_complete && byte_equivalent {
    "verified"
  } else if blockers.is_empty() {
    "pending"
  } else {
    "blocked"
  }
  .to_string();

  RoundTripProof {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    byte_equivalent,
    proof_chain_complete,
    candidate_source: snapshot.build_plan.candidate_source.clone(),
    candidate_artifact: snapshot.build_plan.candidate_object.clone(),
    proof_target: snapshot.build_plan.proof_target.clone(),
    stages,
    blockers,
    evidence: vec![
      "round-trip proof requires recovered source, exact invocation, rebuild proof, objdiff proof, and native byte comparison".to_string(),
      format!("verification_status={}", snapshot.verification.status),
      format!("match_score_status={}", snapshot.verification.match_score.status),
    ],
  }
}

pub fn derive_byte_equivalence_ledger(snapshot: &ProjectSnapshot) -> ByteEquivalenceLedger {
  let matrix = derive_verification_matrix(snapshot);
  let comparison = snapshot.verification.artifact_comparison.as_ref();
  let blocking_rows = matrix
    .rows
    .iter()
    .filter(|row| row.blocking)
    .map(|row| ByteEquivalenceBlockingRow {
      name: row.name.clone(),
      status: row.status.clone(),
      artifact: row.artifact.clone(),
      failure_class: row.failure_class.clone(),
      detail: row.detail.clone(),
    })
    .collect::<Vec<_>>();
  let byte_equivalent = comparison
    .map(|comparison| {
      comparison.byte_equal
        && comparison.section_inventory_equal
        && comparison.symbol_inventory_equal
        && comparison.relocation_inventory_equal
    })
    .unwrap_or(false);
  let status = if byte_equivalent && blocking_rows.is_empty() {
    "verified"
  } else if comparison.is_some() {
    "mismatch"
  } else if blocking_rows.is_empty() {
    "pending"
  } else {
    "blocked"
  }
  .to_string();
  let mut evidence = vec![
    "Byte equivalence is authoritative only when raw bytes and native inventories match.".to_string(),
    "Missing comparison data is not treated as a decompilation mismatch.".to_string(),
    format!("verification_status={}", snapshot.verification.status),
    format!("match_score_status={}", snapshot.verification.match_score.status),
  ];
  if comparison.is_none() {
    evidence.push("artifact_comparison=not-run".to_string());
  }

  ByteEquivalenceLedger {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    byte_equivalent,
    comparison_available: comparison.is_some(),
    proof_target: snapshot.build_plan.proof_target.clone(),
    candidate_artifact: snapshot.build_plan.candidate_object.clone(),
    target_fingerprint: comparison.map(|comparison| comparison.target.clone()),
    candidate_fingerprint: comparison.map(|comparison| comparison.candidate.clone()),
    first_mismatch_offset: comparison.and_then(|comparison| comparison.first_mismatch_offset),
    section_inventory_equal: comparison.map(|comparison| comparison.section_inventory_equal),
    symbol_inventory_equal: comparison.map(|comparison| comparison.symbol_inventory_equal),
    relocation_inventory_equal: comparison.map(|comparison| comparison.relocation_inventory_equal),
    blocking_rows,
    evidence,
  }
}

pub fn derive_compiler_compatibility_ledger(
  snapshot: &ProjectSnapshot,
) -> CompilerCompatibilityLedger {
  let profiles = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .map(|profile| {
      let invocations = snapshot
        .compiler_invocation
        .invocations
        .iter()
        .filter(|invocation| invocation.profile_id == profile.id)
        .collect::<Vec<_>>();
      let exact_invocation_recovered = invocations
        .iter()
        .any(|invocation| invocation.exact_command_recovered);
      let source_systems = profile
        .upstream_evidence
        .iter()
        .map(|evidence| evidence.system.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
      let rust_port_statuses = profile
        .upstream_evidence
        .iter()
        .map(|evidence| evidence.rust_port_status.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
      let has_unresolved_source = profile
        .upstream_evidence
        .iter()
        .any(|evidence| evidence.source_kind == "unresolved");
      let has_public_compiler_source = profile.upstream_evidence.iter().any(|evidence| {
        matches!(evidence.system.as_str(), "llvm" | "gcc")
          && evidence.source_kind == "github-content-file"
      });
      let source_availability = if has_unresolved_source {
        "proprietary-or-unavailable"
      } else if has_public_compiler_source {
        "public-source-modeled"
      } else {
        "analysis-evidence-only"
      };
      let compatibility_status = if exact_invocation_recovered {
        "exact-invocation-recovered"
      } else if has_unresolved_source {
        "proprietary-gap"
      } else if has_public_compiler_source {
        "public-source-modeled"
      } else {
        "evidence-only"
      };
      let modeling_boundary = if exact_invocation_recovered {
        "candidate has exact invocation evidence; byte proof still decides match"
      } else if has_public_compiler_source {
        "public source informs compatibility modeling only; exact version, flags, environment, and emitted bytes remain unproven"
      } else {
        "compiler behavior cannot be implemented from unavailable source; recover from target artifacts and proof runs"
      };
      let mut blockers = profile.uncertainty.clone();
      blockers.extend(invocations.iter().flat_map(|invocation| invocation.blockers.clone()));
      if !exact_invocation_recovered {
        blockers.push("Exact compiler executable, version, argument vector, working directory, environment, runtime inputs, and emitted bytes remain unrecovered.".to_string());
      }
      blockers.sort();
      blockers.dedup();

      CompilerCompatibilityProfile {
        id: profile.id.clone(),
        family: profile.family.clone(),
        vendor: profile.vendor.clone(),
        status: profile.status.clone(),
        compatibility_status: compatibility_status.to_string(),
        source_availability: source_availability.to_string(),
        modeling_boundary: modeling_boundary.to_string(),
        exact_invocation_status: if exact_invocation_recovered {
          "recovered".to_string()
        } else {
          "unresolved".to_string()
        },
        evidence_score: profile.evidence_score,
        evidence_confidence: profile.evidence_confidence.clone(),
        source_systems,
        rust_port_statuses,
        required_components: profile.required_components.clone(),
        blockers,
        evidence: profile
          .ranking_reasons
          .iter()
          .cloned()
          .chain(profile.evidence.iter().cloned())
          .collect(),
      }
    })
    .collect::<Vec<_>>();
  let public_source_modeled_count = profiles
    .iter()
    .filter(|profile| profile.source_availability == "public-source-modeled")
    .count();
  let proprietary_gap_count = profiles
    .iter()
    .filter(|profile| profile.source_availability == "proprietary-or-unavailable")
    .count();
  let exact_invocation_recovered = snapshot.compiler_invocation.recovered_invocation_count > 0;
  let mut blockers = profiles
    .iter()
    .flat_map(|profile| {
      profile
        .blockers
        .iter()
        .map(move |blocker| format!("{}: {}", profile.id, blocker))
    })
    .collect::<Vec<_>>();
  blockers.extend(snapshot.compiler_invocation.missing_evidence.iter().cloned());
  blockers.sort();
  blockers.dedup();
  let status = if exact_invocation_recovered && proprietary_gap_count == 0 {
    "ready-for-proof"
  } else if profiles.is_empty() {
    "unmodeled"
  } else if proprietary_gap_count > 0 {
    "blocked-by-proprietary-gaps"
  } else {
    "invocation-unresolved"
  }
  .to_string();

  CompilerCompatibilityLedger {
    schema_version: 1,
    generated_at: snapshot.build_plan.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    profile_count: profiles.len(),
    public_source_modeled_count,
    proprietary_gap_count,
    exact_invocation_recovered,
    selected_profile: snapshot.build_plan.toolchain.selected_profile.clone(),
    recommended_profile: snapshot.build_plan.toolchain.recommended_profile.clone(),
    profiles,
    blockers,
    evidence: vec![
      "Public compiler source is used to model compatibility boundaries, not to fabricate exact target compiler behavior.".to_string(),
      "Exact byte-equivalent rebuild still requires recovered invocation evidence and object/binary proof.".to_string(),
      format!("compiler_invocation_status={}", snapshot.compiler_invocation.status),
    ],
  }
}

pub fn derive_drift_analysis(snapshot: &ProjectSnapshot) -> DriftAnalysis {
  let roundtrip = derive_roundtrip_proof(snapshot);
  let check = |name: &str| {
    snapshot
      .verification
      .checks
      .iter()
      .find(|check| check.name == name)
  };
  let mut items = Vec::new();
  let source_verification = derive_source_verification_ledger(snapshot);

  if source_verification.verified_source_count == 0
    || source_verification.byte_proved_candidate_count > 0
    || source_verification.unverified_source_count > 0
    || source_verification.blocked_stub_count > 0
    || source_verification.policy_violation_count > 0
  {
    let mut blockers = Vec::new();
    if source_verification.verified_source_count == 0 {
      blockers.push("verified recovered source is unavailable".to_string());
    }
    if source_verification.byte_proved_candidate_count > 0 {
      blockers.push(
        "candidate source is byte-proved but exact compiler invocation remains unresolved"
          .to_string(),
      );
    }
    if source_verification.unverified_source_count > 0 {
      blockers.push("candidate source exists but is not yet marked as verified recovered source".to_string());
    }
    if source_verification.blocked_stub_count > 0 {
      blockers.push("source candidate is not verified recovered logic".to_string());
    }
    if source_verification.policy_violation_count > 0 {
      blockers.push("final source must not contain placeholders or fabricated behavior".to_string());
    }
    items.push(drift_item(
      "source-recovery-drift",
      "source",
      FailureClass::SemanticUnknown,
      "unresolved",
      "critical",
      true,
      format!(
        "{} verified source candidate(s), {} byte-proved candidate(s), {} unverified candidate(s), {} blocking stub(s), and {} policy violation(s).",
        source_verification.verified_source_count,
        source_verification.byte_proved_candidate_count,
        source_verification.unverified_source_count,
        source_verification.blocked_stub_count,
        source_verification.policy_violation_count
      ),
      "source-verification.json",
      "Recovered source must be non-scaffold and pass source policy before rebuild proof.",
      blockers,
      vec![
        format!("source_audit_status={}", snapshot.source_audit.status),
        format!("source_verification_status={}", source_verification.status),
      ],
    ));
  }

  if snapshot.compiler_invocation.recovered_invocation_count == 0 {
    items.push(drift_item(
      "compiler-invocation-drift",
      "compiler",
      FailureClass::CompilerUnknown,
      "unresolved",
      "critical",
      true,
      "Exact compiler/linker invocation is not recovered.".to_string(),
      "compiler-invocation.json",
      "Exact executable identity, version, arguments, environment, include paths, defines, sysroot, libraries, and startup inputs must be recovered.",
      snapshot.compiler_invocation.missing_evidence.clone(),
      vec![
        format!("candidate_invocations={}", snapshot.compiler_invocation.candidate_count),
        format!(
          "recovered_invocations={}",
          snapshot.compiler_invocation.recovered_invocation_count
        ),
      ],
    ));
  }

  if snapshot.cfg_evidence.unresolved_function_count > 0
    || snapshot.cfg_evidence.comparison_readiness.status != "ready"
  {
    items.push(drift_item(
      "cfg-structure-drift",
      "cfg",
      FailureClass::DecompilationDrift,
      "unresolved",
      "high",
      false,
      format!(
        "{} function CFG(s) are unresolved; comparison readiness is {}.",
        snapshot.cfg_evidence.unresolved_function_count,
        snapshot.cfg_evidence.comparison_readiness.status
      ),
      "cfg-evidence.json",
      "Target and candidate CFGs must be derived from verifiable artifacts before CFG comparison can support matching confidence.",
      snapshot.cfg_evidence.comparison_readiness.blockers.clone(),
      vec![
        format!("cfg_status={}", snapshot.cfg_evidence.status),
        format!("cfg_edges={}", snapshot.cfg_evidence.edge_count),
      ],
    ));
  }

  if snapshot.type_relations.unresolved_type_count > 0 {
    items.push(drift_item(
      "type-layout-drift",
      "type-layout",
      FailureClass::SemanticUnknown,
      "unresolved",
      "high",
      false,
      format!(
        "{} type/layout candidate(s) remain unresolved.",
        snapshot.type_relations.unresolved_type_count
      ),
      "type-relations.json",
      "Types, layouts, calling conventions, RTTI, vtables, templates, and ABI facts must be backed by evidence and rebuild proof.",
      snapshot.type_relations.uncertainty.clone(),
      vec![
        format!("type_candidates={}", snapshot.type_relations.type_candidate_count),
        format!("relationships={}", snapshot.type_relations.relationship_count),
      ],
    ));
  }

  if snapshot.dependency_graph.unresolved_dependency_count > 0 {
    items.push(drift_item(
      "dependency-linkage-drift",
      "dependency",
      FailureClass::CompilerUnknown,
      "unresolved",
      "high",
      false,
      format!(
        "{} dependency/link requirement(s) remain unresolved.",
        snapshot.dependency_graph.unresolved_dependency_count
      ),
      "dependency-graph.json",
      "Exact libraries, import libraries, runtime sidecars, startup objects, linker scripts, and search order must be recovered or proven irrelevant.",
      snapshot.dependency_graph.uncertainty.clone(),
      vec![
        format!("imports={}", snapshot.dependency_graph.import_count),
        format!("runtime_artifacts={}", snapshot.dependency_graph.runtime_artifact_count),
      ],
    ));
  }

  let proof_targets = derive_proof_target_ledger(snapshot);
  if proof_targets.mapped_unit_count == 0
    || check("object_match").map(|check| check.status.as_str()) != Some("passed")
  {
    items.push(drift_item(
      "proof-target-drift",
      "proof",
      FailureClass::ProofArtifactMissing,
      "blocked",
      "critical",
      true,
      "Authoritative object proof is unavailable or not passing.".to_string(),
      "objdiff.json",
      "A golden object and candidate object must be compared with objdiff before match claims.",
      check_blockers(check("object_match")),
      vec![
        format!("proof_target={}", snapshot.build_plan.proof_target),
        format!("mapped_proof_target_units={}", proof_targets.mapped_unit_count),
      ],
    ));
  }

  if check("binary_diff").map(|check| check.status.as_str()) == Some("failed")
    || snapshot
      .verification
      .artifact_comparison
      .as_ref()
      .map(|comparison| !comparison.byte_equal)
      .unwrap_or(false)
  {
    items.push(drift_item(
      "binary-byte-drift",
      "binary",
      FailureClass::VerificationMismatch,
      "mismatch",
      "critical",
      true,
      "Native byte comparison reported artifact differences.".to_string(),
      "verification.json",
      "Candidate output must be byte-equivalent to the proof target for a binary-equivalence claim.",
      check_blockers(check("binary_diff")),
      snapshot
        .verification
        .artifact_comparison
        .as_ref()
        .map(|comparison| {
          vec![format!(
            "first_mismatch_offset={}",
            comparison
              .first_mismatch_offset
              .map(|offset| offset.to_string())
              .unwrap_or_else(|| "unknown".to_string())
          )]
        })
        .unwrap_or_else(|| vec!["artifact_comparison=not-run".to_string()]),
    ));
  }

  for provider in snapshot
    .analysis
    .tool_availability
    .analysis_providers
    .iter()
    .filter(|provider| !provider.available && provider.kind != "in-process")
  {
    items.push(drift_item(
      &format!("infra-provider-{}-missing", provider.id),
      "infrastructure",
      FailureClass::ToolMissing,
      "infra_blocked",
      "medium",
      false,
      format!("Analysis provider `{}` is unavailable.", provider.id),
      "analysis.json",
      "External analysis providers should be available for the adapter phases that require them.",
      vec![provider.detail.clone()],
      provider.evidence.clone(),
    ));
  }

  let mut categories = BTreeMap::<String, (usize, usize)>::new();
  for item in &items {
    let entry = categories.entry(item.category.clone()).or_insert((0, 0));
    entry.0 += 1;
    if item.blocking {
      entry.1 += 1;
    }
  }
  let categories = categories
    .into_iter()
    .map(|(category, (count, blocking_count))| DriftCategorySummary {
      category,
      count,
      blocking_count,
    })
    .collect::<Vec<_>>();
  let blocking_drift_count = items.iter().filter(|item| item.blocking).count();
  let status = if roundtrip.byte_equivalent && items.is_empty() {
    "verified"
  } else if blocking_drift_count > 0 {
    "blocked"
  } else if items.is_empty() {
    "pending"
  } else {
    "drift_detected"
  }
  .to_string();

  DriftAnalysis {
    schema_version: 1,
    generated_at: snapshot.verification.generated_at.clone(),
    case_id: snapshot.case.case_id.clone(),
    status,
    drift_count: items.len(),
    blocking_drift_count,
    categories,
    items,
    evidence: vec![
      "Drift analysis is derived from existing ledgers and does not invent recovered source.".to_string(),
      format!("roundtrip_status={}", roundtrip.status),
      format!("byte_equivalent={}", roundtrip.byte_equivalent),
    ],
  }
}

fn drift_item(
  id: &str,
  category: &str,
  failure_class: FailureClass,
  status: &str,
  severity: &str,
  blocking: bool,
  summary: impl Into<String>,
  source_artifact: &str,
  expected_proof: &str,
  blockers: Vec<String>,
  evidence: Vec<String>,
) -> DriftItem {
  DriftItem {
    id: id.to_string(),
    category: category.to_string(),
    failure_class,
    status: status.to_string(),
    severity: severity.to_string(),
    blocking,
    summary: summary.into(),
    source_artifact: source_artifact.to_string(),
    expected_proof: expected_proof.to_string(),
    blockers,
    evidence,
  }
}

fn roundtrip_stage(
  name: &str,
  status: &str,
  authoritative: bool,
  artifact: &str,
  detail: String,
  blockers: Vec<String>,
) -> RoundTripStage {
  RoundTripStage {
    name: name.to_string(),
    status: status.to_string(),
    authoritative,
    artifact: artifact.to_string(),
    detail,
    blockers,
  }
}

fn check_detail(check: Option<&VerificationCheck>) -> String {
  check
    .map(|check| check.detail.clone())
    .unwrap_or_else(|| "Verification check has not been recorded.".to_string())
}

fn check_blockers(check: Option<&VerificationCheck>) -> Vec<String> {
  match check {
    Some(check) if check.status == "passed" => Vec::new(),
    Some(check) => vec![check.detail.clone()],
    None => vec!["verification check missing".to_string()],
  }
}

fn verification_authority(name: &str, advisory: bool) -> &'static str {
  match name {
    "object_match" | "binary_diff" | "rebuild_proof" => "authoritative",
    "source_artifact_audit" | "compiler_invocation_contract" => "policy",
    _ if advisory => "advisory",
    _ => "authoritative",
  }
}

fn verification_row_blocking(name: &str, authority: &str, status: &str) -> bool {
  match authority {
    "authoritative" => matches!(status, "failed" | "skipped"),
    "policy" => name == "compiler_invocation_contract" && status == "failed",
    _ => false,
  }
}

fn verification_domain(name: &str) -> &'static str {
  match name {
    "object_match" => "object",
    "binary_diff" | "section_comparison" => "binary",
    "symbol_comparison" => "symbol",
    "relocation_comparison" | "relocation_inventory" => "relocation",
    "cfg_comparison" | "cfg_inventory" | "function_boundary_inventory" => "cfg",
    "symbol_type_comparison" | "type_relation_inventory" => "symbol-type",
    "source_artifact_audit" => "source-policy",
    "compiler_invocation_contract" => "compiler",
    "dependency_inventory" => "dependency",
    "debug_inventory" => "debug",
    "toolchain_fingerprint" => "toolchain",
    "binary_fingerprint" | "section_inventory" | "symbol_inventory" => "native-inventory",
    "analysis_provider_availability" | "toolchain_host_availability" => "infrastructure",
    "rebuild_proof" => "rebuild",
    _ => "unknown",
  }
}

fn verification_artifact(name: &str) -> &'static str {
  match name {
    "object_match" => "objdiff.json",
    "rebuild_proof" => "build-plan.json",
    "cfg_comparison" | "cfg_inventory" | "function_boundary_inventory" => "cfg-evidence.json",
    "symbol_type_comparison" | "type_relation_inventory" => "type-relations.json",
    "dependency_inventory" => "dependency-graph.json",
    "source_artifact_audit" => "source-audit.json",
    "compiler_invocation_contract" => "compiler-invocation.json",
    "analysis_provider_availability" | "toolchain_host_availability" => "toolchain-manifest.json",
    "binary_fingerprint"
    | "section_inventory"
    | "symbol_inventory"
    | "relocation_inventory"
    | "debug_inventory"
    | "toolchain_fingerprint" => "analysis.json",
    _ => "verification.json",
  }
}

fn verification_failure_class(name: &str, status: &str) -> Option<FailureClass> {
  if status != "failed" {
    return None;
  }
  match name {
    "object_match" | "binary_diff" | "section_comparison" | "symbol_comparison"
    | "relocation_comparison" => Some(FailureClass::VerificationMismatch),
    "rebuild_proof" => Some(FailureClass::RebuildFailed),
    "compiler_invocation_contract" => Some(FailureClass::CompilerUnknown),
    "source_artifact_audit" | "cfg_comparison" | "symbol_type_comparison" => {
      Some(FailureClass::SemanticUnknown)
    }
    "analysis_provider_availability" | "toolchain_host_availability" => {
      Some(FailureClass::InfraError)
    }
    _ => None,
  }
}

#[derive(Debug, Clone)]
pub struct AttemptPlan {
  pub rows: Vec<AttemptPlanRow>,
  pub actionable_attempt_count: usize,
  pub host_ready_count: usize,
  pub top_attempts: Vec<StatusAttempt>,
  pub next_actions: Vec<StatusNextAction>,
}

#[derive(Debug, Clone)]
pub struct AttemptPlanRow {
  pub id: String,
  pub profile_id: String,
  pub profile_score: u32,
  pub profile_confidence: String,
  pub backend_id: String,
  pub profile_family: String,
  pub backend_family: String,
  pub backend_generator: String,
  pub role: String,
  pub row_status: String,
  pub status_reason: String,
  pub host_status: String,
  pub host_ready: bool,
  pub proof_status: String,
  pub rebuild_status: String,
  pub exact_invocation_status: String,
  pub priority: u32,
  pub priority_class: String,
  pub priority_reasons: Vec<String>,
  pub next_action: String,
}

pub fn derive_attempt_plan(snapshot: &ProjectSnapshot) -> AttemptPlan {
  let proof_targets = derive_proof_target_ledger(snapshot);
  let proof_target_available = proof_targets.mapped_unit_count > 0;
  let rebuild_check = snapshot
    .verification
    .checks
    .iter()
    .find(|check| check.name == "rebuild_proof");
  let object_match_check = snapshot
    .verification
    .checks
    .iter()
    .find(|check| check.name == "object_match");

  let mut rows = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .flat_map(|profile| {
      snapshot
        .build_plan
        .build_system
        .candidate_backends
        .iter()
        .map(|backend| {
            let host_ready = profile
              .required_components
              .iter()
              .filter(|component| {
                matches!(component.kind.as_str(), "compiler" | "assembler" | "linker")
              })
              .all(|component| {
                command_candidates_for_component(&component.name)
                  .into_iter()
                  .any(|candidate| command_exists_normalized(&candidate))
              });
            let rebuild_state = if snapshot.actions.rebuild {
              rebuild_check
                .map(|check| check.status.as_str())
                .unwrap_or("not-recorded")
            } else {
              "not-requested"
            };
            let proof_status = if !proof_target_available {
              "missing-proof-target"
            } else {
              object_match_check
                .map(|check| check.status.as_str())
                .unwrap_or("pending")
            };
            let exact_invocation_status = if snapshot.actions.rebuild {
              if rebuild_check.map(|check| check.status.as_str()) == Some("passed") {
                "recovered"
              } else if rebuild_check.is_some() {
                "scaffold-only"
              } else {
                "unresolved"
              }
            } else {
              "unresolved"
            };
            let row_status = classify_attempt_row(
              host_ready,
              proof_target_available,
              rebuild_state,
              proof_status,
              exact_invocation_status,
            );
            let host_status = if host_ready {
              "candidate-executables-present"
            } else {
              "missing-candidate-executables"
            };
            let status_reason = attempt_row_reason(
              row_status,
              host_ready,
              proof_target_available,
              rebuild_check,
              object_match_check,
            );
            let (priority, priority_class, priority_reasons, next_action) =
              attempt_row_priority(
                row_status,
                profile.evidence_score,
                host_ready,
                proof_target_available,
                rebuild_state,
                proof_status,
                exact_invocation_status,
              );

            AttemptPlanRow {
              id: format!("{}::{}", profile.id, backend.id),
              profile_id: profile.id.clone(),
              profile_score: profile.evidence_score,
              profile_confidence: profile.evidence_confidence.clone(),
              backend_id: backend.id.clone(),
              profile_family: profile.family.clone(),
              backend_family: backend.family.clone(),
              backend_generator: backend.generator.clone(),
              role: profile.role.clone(),
              row_status: row_status.to_string(),
              status_reason,
              host_status: host_status.to_string(),
              host_ready,
              proof_status: proof_status.to_string(),
              rebuild_status: rebuild_state.to_string(),
              exact_invocation_status: exact_invocation_status.to_string(),
              priority,
              priority_class: priority_class.to_string(),
              priority_reasons,
              next_action,
            }
          })
    })
    .collect::<Vec<_>>();

  rows.sort_by(|left, right| {
    right
      .priority
      .cmp(&left.priority)
      .then_with(|| right.host_ready.cmp(&left.host_ready))
      .then_with(|| left.id.cmp(&right.id))
  });

  let actionable_attempt_count = rows
    .iter()
    .filter(|row| {
      matches!(
        row.row_status.as_str(),
        "invocation_unresolved" | "scaffold_attempted" | "verification_mismatch" | "match_candidate"
      )
    })
    .count();
  let host_ready_count = rows.iter().filter(|row| row.host_ready).count();

  let next_actions = rows.iter().fold(Vec::<StatusNextAction>::new(), |mut acc, row| {
    if let Some(existing) = acc.iter_mut().find(|item| item.action == row.next_action) {
      if row.priority > existing.priority {
        existing.priority = row.priority;
      }
    } else {
      acc.push(StatusNextAction {
        priority: row.priority,
        action: row.next_action.clone(),
      });
    }
    acc
  });
  let mut next_actions = next_actions;
  next_actions.sort_by(|left, right| {
    right
      .priority
      .cmp(&left.priority)
      .then_with(|| left.action.cmp(&right.action))
  });
  next_actions.truncate(5);

  let top_attempts = rows
    .iter()
    .take(3)
    .map(|row| StatusAttempt {
      id: row.id.clone(),
      row_status: row.row_status.clone(),
      priority: row.priority,
      priority_class: row.priority_class.clone(),
      next_action: row.next_action.clone(),
      host_ready: row.host_ready,
    })
    .collect::<Vec<_>>();

  AttemptPlan {
    rows,
    actionable_attempt_count,
    host_ready_count,
    top_attempts,
    next_actions,
  }
}

fn classify_attempt_row(
  host_ready: bool,
  proof_target_available: bool,
  rebuild_state: &str,
  proof_status: &str,
  exact_invocation_status: &str,
) -> &'static str {
  if !host_ready {
    "infra_blocked"
  } else if !proof_target_available {
    "proof_blocked"
  } else if rebuild_state == "failed" && exact_invocation_status == "scaffold-only" {
    "scaffold_attempted"
  } else if proof_status == "failed" {
    "verification_mismatch"
  } else if rebuild_state == "passed" && proof_status == "passed" {
    "match_candidate"
  } else {
    "invocation_unresolved"
  }
}

fn attempt_row_reason(
  row_status: &str,
  host_ready: bool,
  proof_target_available: bool,
  rebuild_check: Option<&VerificationCheck>,
  object_match_check: Option<&VerificationCheck>,
) -> String {
  match row_status {
    "infra_blocked" if !host_ready => {
      "Host candidate compiler/assembler/linker executables are missing.".to_string()
    }
    "proof_blocked" if !proof_target_available => {
      "Golden proof target has not been recovered yet.".to_string()
    }
    "scaffold_attempted" => rebuild_check
      .map(|check| format!("Rebuild was attempted but remained scaffold-only: {}", check.detail))
      .unwrap_or_else(|| "Rebuild attempt evidence is incomplete.".to_string()),
    "verification_mismatch" => object_match_check
      .map(|check| format!("Object proof ran and did not match: {}", check.detail))
      .unwrap_or_else(|| "Verification mismatch evidence is incomplete.".to_string()),
    "match_candidate" => {
      "Rebuild and object proof both passed for this profile/backend row.".to_string()
    }
    _ => "Exact invocation, flags, and environment remain unresolved.".to_string(),
  }
}

fn attempt_row_priority(
  row_status: &str,
  profile_score: u32,
  host_ready: bool,
  proof_target_available: bool,
  rebuild_state: &str,
  proof_status: &str,
  exact_invocation_status: &str,
) -> (u32, &'static str, Vec<String>, String) {
  let mut score = profile_score.min(100);
  let mut reasons = vec![format!("compiler profile evidence score={profile_score}")];

  let (priority_class, next_action, status_boost, status_reason) = match row_status {
    "match_candidate" => (
      "integration-review",
      "Review proof artifacts and integrate only if object and binary verification remain passing.",
      35,
      "row already reached passing rebuild/proof state",
    ),
    "invocation_unresolved" => (
      "invocation-recovery",
      "Recover the exact compiler/linker invocation for this profile/backend pair and rerun rebuild proof.",
      30,
      "host/proof surface looks plausible but exact invocation is still unresolved",
    ),
    "scaffold_attempted" => (
      "replace-scaffold",
      "Replace the scaffold compile path with the exact recovered invocation, then rerun proof.",
      26,
      "a rebuild path exists, but it is still scaffold-only",
    ),
    "verification_mismatch" => (
      "mismatch-triage",
      "Compare mismatch evidence to recover missing flags, ABI settings, libraries, or linker behavior.",
      20,
      "proof ran and produced mismatch evidence worth triaging",
    ),
    "proof_blocked" => (
      "proof-target-recovery",
      "Recover or point Mizuchi at the golden proof target before rebuild proof can proceed.",
      12,
      "proof target is the main blocker for this attempt family",
    ),
    "infra_blocked" => (
      "host-tooling",
      "Install or locate the missing host compiler/assembler/linker executables for this attempt family.",
      8,
      "host tooling is missing for this attempt family",
    ),
    _ => (
      "queued",
      "Collect more target/toolchain evidence before scheduling this attempt.",
      0,
      "attempt remains informational rather than actionable",
    ),
  };
  score = (score + status_boost).min(100);
  reasons.push(status_reason.to_string());

  if host_ready {
    score = (score + 5).min(100);
    reasons.push("host candidate executables are available".to_string());
  }
  if proof_target_available {
    score = (score + 5).min(100);
    reasons.push("golden proof target is available".to_string());
  }
  if rebuild_state == "passed" {
    score = (score + 5).min(100);
    reasons.push("rebuild evidence shows a passing compile stage".to_string());
  }
  if proof_status == "passed" {
    score = (score + 5).min(100);
    reasons.push("proof evidence is currently passing for this row".to_string());
  }
  if exact_invocation_status == "recovered" {
    score = (score + 5).min(100);
    reasons.push("exact invocation status is recovered".to_string());
  }

  (score, priority_class, reasons, next_action.to_string())
}

pub fn command_candidates_for_component(component: &str) -> Vec<String> {
  match component {
    "cl" => vec!["cl.exe".to_string(), "cl".to_string()],
    "link" => vec!["link.exe".to_string(), "link".to_string()],
    "ml-or-ml64" => vec![
      "ml.exe".to_string(),
      "ml64.exe".to_string(),
      "ml".to_string(),
      "ml64".to_string(),
    ],
    "clang-cl" => vec!["clang-cl".to_string(), "clang-cl.exe".to_string()],
    "link-or-lld-link" => vec!["link.exe".to_string(), "lld-link".to_string()],
    "cc1" => vec!["gcc".to_string(), "cc".to_string()],
    "gcc" => vec!["gcc".to_string()],
    "as" => vec!["as".to_string()],
    "ld" => vec!["ld".to_string()],
    "clang" => vec!["clang".to_string()],
    "llvm-mc" => vec!["llvm-mc".to_string()],
    "lld-or-platform-linker" => vec!["lld".to_string(), "ld".to_string(), "ld64".to_string()],
    "compiler-driver" => vec!["cc".to_string(), "gcc".to_string(), "clang".to_string()],
    "assembler" => vec!["as".to_string(), "llvm-mc".to_string(), "gas".to_string()],
    "linker" => vec![
      "ld".to_string(),
      "lld".to_string(),
      "ld.lld".to_string(),
      "link.exe".to_string(),
    ],
    "ld64-compatible-linker" => vec!["ld64".to_string(), "ld".to_string(), "lld".to_string()],
    "cc" => vec!["cc".to_string()],
    "cfe" => vec!["cfe".to_string()],
    "uopt-or-ugen" => vec!["uopt".to_string(), "ugen".to_string()],
    "as1" => vec!["as1".to_string()],
    "rustc" => vec!["rustc".to_string()],
    "llvm-backend" => vec!["llc".to_string(), "rustc".to_string()],
    _ => vec![component.to_string()],
  }
}

pub fn probe_component_command_availability(component: &str) -> Vec<CommandAvailability> {
  command_candidates_for_component(component)
    .iter()
    .map(|candidate| probe_command_availability(candidate))
    .collect()
}

pub fn probe_command_availability(name: &str) -> CommandAvailability {
  let version_probe_enabled = env_flag_enabled_model("DECOMP_PROBE_TOOL_VERSIONS");
  let cache_key = format!(
    "{}|{}|{}",
    name.to_ascii_lowercase(),
    version_probe_enabled,
    std::env::var("PATH").unwrap_or_default()
  );
  let cache = command_probe_cache();
  if let Some(cached) = cache
    .lock()
    .expect("command probe cache mutex poisoned")
    .get(&cache_key)
    .cloned()
  {
    return cached;
  }

  let availability = build_command_availability(name, version_probe_enabled);
  cache
    .lock()
    .expect("command probe cache mutex poisoned")
    .insert(cache_key, availability.clone());
  availability
}

pub fn command_exists_normalized(name: &str) -> bool {
  probe_command_availability(name).installed
}

fn command_probe_cache() -> &'static Mutex<BTreeMap<String, CommandAvailability>> {
  static CACHE: OnceLock<Mutex<BTreeMap<String, CommandAvailability>>> = OnceLock::new();
  CACHE.get_or_init(|| Mutex::new(BTreeMap::new()))
}

fn build_command_availability(name: &str, version_probe_enabled: bool) -> CommandAvailability {
  let Some(path) = resolve_command_path_normalized(name) else {
    return CommandAvailability {
      name: name.to_string(),
      installed: false,
      resolved_path: None,
      probe_status: "missing".to_string(),
      version_probe: None,
      version_output: None,
    };
  };

  let mut availability = CommandAvailability {
    name: name.to_string(),
    installed: true,
    resolved_path: Some(path.display().to_string()),
    probe_status: "resolved-path".to_string(),
    version_probe: None,
    version_output: None,
  };

  if version_probe_enabled {
    if let Some((flag, output)) = probe_command_version(&path, name) {
      availability.probe_status = "version-probed".to_string();
      availability.version_probe = Some(flag);
      availability.version_output = Some(output);
    } else {
      availability.probe_status = "version-probe-failed".to_string();
    }
  }

  availability
}

fn resolve_command_path_normalized(name: &str) -> Option<PathBuf> {
  command_search_names(name).into_iter().find_map(resolve_command_path)
}

fn command_search_names(name: &str) -> Vec<String> {
  let mut names = Vec::new();
  let trimmed = name.trim();
  if trimmed.is_empty() {
    return names;
  }
  names.push(trimmed.to_string());
  if let Some(without_exe) = trimmed.strip_suffix(".exe") {
    if !without_exe.is_empty() {
      if !names.iter().any(|item| item == without_exe) {
        names.push(without_exe.to_string());
      }
    }
  } else if !trimmed.contains('.') {
    let with_exe = format!("{trimmed}.exe");
    if !names.iter().any(|item| item == &with_exe) {
      names.push(with_exe);
    }
  }
  names
}

fn resolve_command_path(name: String) -> Option<PathBuf> {
  std::env::var_os("PATH").and_then(|paths| {
    std::env::split_paths(&paths)
      .map(|path| path.join(&name))
      .find(|candidate| candidate.is_file())
  })
}

fn probe_command_version(path: &Path, name: &str) -> Option<(String, String)> {
  version_probe_flags(name).into_iter().find_map(|flag| {
    let output = Command::new(path)
      .arg(&flag)
      .env("LC_ALL", "C")
      .env("LANG", "C")
      .output()
      .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{}\n{}", stdout.trim(), stderr.trim())
      .lines()
      .map(str::trim)
      .filter(|line| !line.is_empty())
      .take(4)
      .collect::<Vec<_>>()
      .join(" | ");
    (!combined.is_empty()).then(|| (flag, truncate_probe_output(&combined)))
  })
}

fn version_probe_flags(name: &str) -> Vec<String> {
  let basename = Path::new(name)
    .file_name()
    .and_then(|item| item.to_str())
    .unwrap_or(name)
    .to_ascii_lowercase();
  let mut flags = Vec::new();
  if basename == "cl" || basename == "cl.exe" {
    flags.push("/Bv".to_string());
  }
  flags.extend(["--version", "-v", "-V"].iter().map(|item| (*item).to_string()));
  flags
}

fn truncate_probe_output(value: &str) -> String {
  const MAX_CHARS: usize = 240;
  if value.chars().count() <= MAX_CHARS {
    value.to_string()
  } else {
    let truncated = value.chars().take(MAX_CHARS).collect::<String>();
    format!("{truncated}...")
  }
}

fn env_flag_enabled_model(name: &str) -> bool {
  std::env::var(name)
    .map(|value| {
      let normalized = value.trim().to_ascii_lowercase();
      !normalized.is_empty() && normalized != "0" && normalized != "false" && normalized != "no"
    })
    .unwrap_or(false)
}

#[derive(Debug, Clone, Copy)]
pub enum ReportFormat {
  Json,
  Markdown,
}

impl ReportFormat {
  pub fn parse(value: &str) -> Result<Self> {
    match value {
      "json" => Ok(Self::Json),
      "md" => Ok(Self::Markdown),
      other => anyhow::bail!("unsupported report format: {other}"),
    }
  }
}

pub fn default_case_manifest(target: &TargetInput, adapter: &AdapterDescriptor) -> CaseManifest {
  let case_id = target.case_id();
  let binary_path = target.path.display().to_string();
  let symbol_name = format!("BINARY_{}", case_id.to_ascii_uppercase());
  CaseManifest {
    schema_version: 1,
    case_id: case_id.clone(),
    adapter: CaseAdapter {
      id: adapter.id.clone(),
      capabilities_profile: adapter.capabilities_profile.clone(),
    },
    ingest: CaseIngest {
      source_type: adapter.source_type.clone(),
      source_path: binary_path.clone(),
      provenance: "direct".to_string(),
    },
    target: CaseTarget {
      family: adapter.family.clone(),
      binary: binary_path.clone(),
      platform: adapter.platform.clone(),
    },
    load: CaseLoad {
      tool: adapter.load_tool.clone(),
      analysis_providers: adapter.analysis_providers.clone(),
      program_path: binary_path,
      context_path: adapter.context_path.clone(),
    },
    symbol: CaseSymbol {
      name: symbol_name,
      locator: format!("file:{}", target.file_name()),
    },
    proof: CaseProof {
      target_object_path: "unavailable".to_string(),
      source: "missing".to_string(),
      comparator: "objdiff".to_string(),
    },
    workspace: CaseWorkspace {
      prompt_path: ".".to_string(),
      build_dir: "build".to_string(),
    },
  }
}

pub fn default_reconstruction(
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  candidate_path: &str,
) -> ReconstructionGraph {
  let candidate_profile_ids = inferred_profile_ids(analysis);
  let candidate_profiles = infer_compiler_profiles(
    analysis,
    &[
      format!("file_kind={}", analysis.target.file_kind),
      format!("architecture={}", analysis.target.architecture),
      format!("compiler={}", analysis.target.toolchain.compiler),
      format!("linker={}", analysis.target.toolchain.linker),
    ],
  );
  let link_inputs = infer_link_inputs(analysis, &candidate_profiles);
  let runtime_artifacts = infer_runtime_artifacts(analysis, &candidate_profiles);
  let is_archive = !analysis.target.archive_members.is_empty();
  let translation_units = if is_archive {
    analysis
      .target
      .archive_members
      .iter()
      .map(|member| TranslationUnit {
        id: member.id.clone(),
        source_path: format!("sources/candidates/{}.c", member.id),
        object_path: format!("build/members/{}.o", member.id),
        language: "c".to_string(),
        kind: "archive-member-candidate".to_string(),
        status: "blocked".to_string(),
        proof_target: "unavailable".to_string(),
        compiler_profile_candidates: candidate_profile_ids.clone(),
        blocking_reasons: vec![
          "Recovered logic has not been verified for this archive member.".to_string(),
          "Compiler invocation, preprocessor state, archive index behavior, and ABI mode remain unresolved.".to_string(),
        ],
        evidence: vec![
          format!("archive_member={}", member.name),
          format!("member_file_kind={}", member.file_kind),
          format!("member_architecture={}", member.architecture),
          format!("member_functions={}", member.function_count),
          format!("member_symbols={}", member.symbol_count),
        ],
      })
      .collect()
  } else {
    vec![TranslationUnit {
      id: format!("{}_main", case.case_id),
      source_path: candidate_path.to_string(),
      object_path: "build/candidate.o".to_string(),
      language: "c".to_string(),
      kind: "reconstruction-candidate".to_string(),
      status: "blocked".to_string(),
      proof_target: case.proof.target_object_path.clone(),
      compiler_profile_candidates: candidate_profile_ids.clone(),
      blocking_reasons: vec![
        "Recovered logic has not been verified for this translation unit.".to_string(),
        "Compiler invocation, preprocessor state, and ABI mode remain unresolved.".to_string(),
      ],
      evidence: vec![
        format!("file_kind={}", analysis.target.file_kind),
        format!("architecture={}", analysis.target.architecture),
        format!("symbol_count={}", analysis.target.symbols.len()),
        format!("function_count={}", analysis.target.functions.len()),
      ],
    }]
  };
  let source_candidates = if is_archive {
    analysis
      .target
      .archive_members
      .iter()
      .map(|member| SourceCandidate {
        path: format!("sources/candidates/{}.c", member.id),
        language: "c".to_string(),
        kind: "archive-member-blocked-stub".to_string(),
        status: "incomplete".to_string(),
        blocking_reasons: vec![
          "No verified recovered logic has been produced for this archive member yet.".to_string(),
          "Static library reconstruction is currently evidence-first and proof-blocked.".to_string(),
        ],
      })
      .collect()
  } else {
    vec![SourceCandidate {
      path: candidate_path.to_string(),
      language: "c".to_string(),
      kind: "blocked-stub".to_string(),
      status: "incomplete".to_string(),
      blocking_reasons: vec![
        "No verified recovered logic has been produced yet.".to_string(),
        "The Rust orchestrator slice records evidence and uncertainty before synthesis.".to_string(),
      ],
    }]
  };
  let link_units = if is_archive {
    vec![LinkUnit {
      id: format!("{}_archive", case.case_id),
      artifact_path: format!("build/lib{}.a", case.case_id),
      kind: "static-library".to_string(),
      status: "blocked".to_string(),
      linker_profile_candidates: candidate_profile_ids.clone(),
      dependency_libraries: analysis
        .target
        .imports
        .iter()
        .map(|import| import.library.clone())
        .collect(),
      link_inputs,
      runtime_artifacts,
      blocking_reasons: vec![
        "Archive-member source is not recovered or verified.".to_string(),
        "Archive index ordering, librarian behavior, and per-member proof targets are unresolved.".to_string(),
      ],
    }]
  } else {
    vec![LinkUnit {
      id: format!("{}_artifact", case.case_id),
      artifact_path: "build/candidate.o".to_string(),
      kind: "relocatable-object".to_string(),
      status: "blocked".to_string(),
      linker_profile_candidates: candidate_profile_ids,
      dependency_libraries: analysis
        .target
        .imports
        .iter()
        .map(|import| import.library.clone())
        .collect(),
      link_inputs,
      runtime_artifacts,
      blocking_reasons: vec![
        "Only object-level reconstruction scaffolding is available in this slice.".to_string(),
        "Linkable project structure is deferred until exact compiler and dependency inputs are proven.".to_string(),
      ],
    }]
  };
  ReconstructionGraph {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case.case_id.clone(),
    state: "partial".to_string(),
    source_candidates,
    sections: analysis.target.sections.clone(),
    segments: analysis.target.segments.clone(),
    symbols: analysis.target.symbols.clone(),
    dynamic_symbols: analysis.target.dynamic_symbols.clone(),
    functions: analysis.target.functions.clone(),
    relocations: analysis.target.relocations.clone(),
    imports: analysis.target.imports.clone(),
    exports: analysis.target.exports.clone(),
    project_structure: ProjectStructure {
      source_roots: vec!["sources".to_string()],
      include_roots: vec!["context".to_string()],
      build_roots: vec!["build".to_string()],
      artifact_roots: vec!["build".to_string()],
      translation_units,
      link_units,
      notes: vec![
        "Project structure records truthful compile/link boundaries before source recovery exists.".to_string(),
        "Address facts remain in metadata and evidence, not emitted as recovered source.".to_string(),
      ],
    },
    debug: analysis.target.debug.clone(),
    toolchain: analysis.target.toolchain.clone(),
    toolchain_hypothesis: vec![
      format!("file_kind={}", analysis.target.file_kind),
      format!("architecture={}", analysis.target.architecture),
      format!("entry_point={:#x}", analysis.target.entry_point),
      format!("sections={}", analysis.target.sections.len()),
      format!("segments={}", analysis.target.segments.len()),
      format!("symbols={}", analysis.target.symbols.len()),
      format!("dynamic_symbols={}", analysis.target.dynamic_symbols.len()),
      format!("functions={}", analysis.target.functions.len()),
      format!("relocations={}", analysis.target.relocations.len()),
      format!("imports={}", analysis.target.imports.len()),
      format!("exports={}", analysis.target.exports.len()),
      format!("debug_symbols={}", analysis.target.debug.has_debug_symbols),
      format!("compiler={}", analysis.target.toolchain.compiler),
      format!("linker={}", analysis.target.toolchain.linker),
    ],
  }
}

pub fn default_type_relation_graph(case: &CaseManifest, analysis: &AnalysisRecord) -> TypeRelationGraph {
  let mut all_symbols = analysis.target.symbols.clone();
  all_symbols.extend(analysis.target.dynamic_symbols.clone());
  all_symbols.sort_by(|left, right| {
    left
      .address
      .cmp(&right.address)
      .then_with(|| left.name.cmp(&right.name))
  });
  all_symbols.dedup_by(|left, right| {
    left.name == right.name && left.address == right.address && left.kind == right.kind
  });

  let symbols = all_symbols
    .iter()
    .map(type_symbol_node)
    .collect::<Vec<_>>();
  let mut type_candidates = Vec::new();
  let mut relationships = Vec::new();

  for function in &analysis.target.functions {
    let id = format!("type:function:{}", sanitize_identifier(&function.name));
    type_candidates.push(TypeCandidate {
      id: id.clone(),
      kind: "function-signature".to_string(),
      name: function.name.clone(),
      status: "unresolved".to_string(),
      confidence: function.confidence.clone(),
      source_symbols: vec![function.name.clone()],
      evidence: vec![
        "function boundary observed from symbol table".to_string(),
        format!("cfg_status={}", function.cfg_status),
        "parameter types, return type, calling convention, and local variable layout are unresolved".to_string(),
      ],
    });
    relationships.push(TypeRelationship {
      from: format!("symbol:{}", sanitize_identifier(&function.name)),
      to: id,
      kind: "has-unresolved-function-signature".to_string(),
      status: "evidence-only".to_string(),
      evidence: vec![format!("function_source={}", function.source)],
    });
  }

  for symbol in &all_symbols {
    let lower = symbol.name.to_ascii_lowercase();
    let symbol_id = format!("symbol:{}", sanitize_identifier(&symbol.name));
    let mut push_candidate = |kind: &str, confidence: &str, evidence: Vec<String>| {
      let id = format!("type:{}:{}", kind, sanitize_identifier(&symbol.name));
      type_candidates.push(TypeCandidate {
        id: id.clone(),
        kind: kind.to_string(),
        name: symbol.name.clone(),
        status: "unresolved".to_string(),
        confidence: confidence.to_string(),
        source_symbols: vec![symbol.name.clone()],
        evidence,
      });
      relationships.push(TypeRelationship {
        from: symbol_id.clone(),
        to: id,
        kind: format!("indicates-{kind}"),
        status: "evidence-only".to_string(),
        evidence: vec![
          format!("symbol_kind={}", symbol.kind),
          format!("symbol_scope={}", symbol.scope),
        ],
      });
    };

    if symbol.name.starts_with("_ZTV") || lower.contains("vtable") || symbol.name.starts_with("??_7") {
      push_candidate(
        "vtable",
        "medium",
        vec![
          "symbol name matches known C++ vtable naming evidence".to_string(),
          "class layout and virtual dispatch targets are not recovered".to_string(),
        ],
      );
    }
    if symbol.name.starts_with("_ZTI")
      || symbol.name.starts_with("_ZTS")
      || lower.contains("typeinfo")
      || lower.contains("rtti")
    {
      push_candidate(
        "rtti",
        "medium",
        vec![
          "symbol name matches known RTTI/typeinfo naming evidence".to_string(),
          "type hierarchy and object layout remain unresolved".to_string(),
        ],
      );
    }
    if symbol.name.contains('<') || (symbol.name.starts_with("_Z") && symbol.name.contains('I')) {
      push_candidate(
        "template-or-generic-instantiation",
        "low",
        vec![
          "symbol name carries possible template/generic instantiation evidence".to_string(),
          "template parameters are not recovered without verified demangling and type proof".to_string(),
        ],
      );
    }
  }

  type_candidates.sort_by(|left, right| left.id.cmp(&right.id));
  type_candidates.dedup_by(|left, right| left.id == right.id);
  relationships.sort_by(|left, right| {
    left
      .from
      .cmp(&right.from)
      .then_with(|| left.to.cmp(&right.to))
      .then_with(|| left.kind.cmp(&right.kind))
  });
  relationships.dedup_by(|left, right| {
    left.from == right.from && left.to == right.to && left.kind == right.kind
  });
  let unresolved_type_count = type_candidates
    .iter()
    .filter(|candidate| candidate.status == "unresolved")
    .count();
  let status = if type_candidates.is_empty() {
    "no-type-evidence"
  } else {
    "evidence-only"
  };

  TypeRelationGraph {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case.case_id.clone(),
    status: status.to_string(),
    symbol_count: symbols.len(),
    type_candidate_count: type_candidates.len(),
    relationship_count: relationships.len(),
    unresolved_type_count,
    symbols,
    type_candidates,
    relationships,
    uncertainty: vec![
      "No recovered type layout is authoritative without debug info, ABI evidence, or verified decompiler/type-analysis output.".to_string(),
      "Mangled-name classification is evidence only; demangled names and class layouts are not fabricated.".to_string(),
      "Function signatures remain unresolved until parameter, return, calling-convention, and stack/register evidence is verified.".to_string(),
    ],
  }
}

pub fn default_cfg_evidence_graph(case: &CaseManifest, analysis: &AnalysisRecord) -> CfgEvidenceGraph {
  let functions = analysis
    .target
    .functions
    .iter()
    .map(|function| {
      let recovered = function.cfg_status == "recovered";
      CfgFunctionEvidence {
        id: format!("cfg:function:{}", sanitize_identifier(&function.name)),
        name: function.name.clone(),
        status: if recovered {
          "recovered".to_string()
        } else {
          "unresolved".to_string()
        },
        confidence: function.confidence.clone(),
        boundary_source: function.source.clone(),
        basic_block_count: None,
        edge_count: None,
        evidence: vec![
          "function boundary observed before CFG recovery".to_string(),
          format!("boundary_source={}", function.source),
          format!("symbol_size={}", function.size),
          format!("cfg_status={}", function.cfg_status),
        ],
        missing_evidence: vec![
          "instruction-level disassembly for the function".to_string(),
          "basic block split points".to_string(),
          "branch/call/fallthrough edge classification".to_string(),
          "candidate rebuilt CFG for comparison".to_string(),
          "binary/object diff proof tying CFG shape to rebuilt artifact".to_string(),
        ],
      }
    })
    .collect::<Vec<_>>();
  let recovered_function_count = functions
    .iter()
    .filter(|function| function.status == "recovered")
    .count();
  let unresolved_function_count = functions
    .iter()
    .filter(|function| function.status != "recovered")
    .count();
  let status = if functions.is_empty() {
    "no-function-boundaries"
  } else if unresolved_function_count == 0 {
    "cfg-recovered"
  } else {
    "evidence-only"
  };
  let target_cfg_available = recovered_function_count > 0 && unresolved_function_count == 0;

  CfgEvidenceGraph {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case.case_id.clone(),
    status: status.to_string(),
    function_count: functions.len(),
    recovered_function_count,
    unresolved_function_count,
    edge_count: 0,
    functions,
    edges: Vec::new(),
    comparison_readiness: CfgComparisonReadiness {
      status: "blocked".to_string(),
      target_cfg_available,
      candidate_cfg_available: false,
      comparison_artifact: "cfg-comparison.json".to_string(),
      blockers: vec![
        "target CFG edges are not recovered by the Rust slice yet".to_string(),
        "candidate rebuilt CFG is unavailable until verified source and object artifacts exist"
          .to_string(),
        "Mizuchi will not fabricate basic blocks, branch targets, or CFG edges from symbol boundaries"
          .to_string(),
      ],
    },
    uncertainty: vec![
      "Function boundaries are not control-flow graphs.".to_string(),
      "CFG comparison remains advisory until target and candidate CFGs are both derived from verifiable artifacts.".to_string(),
      "No basic blocks, branch targets, or edges are emitted without analyzer evidence.".to_string(),
    ],
  }
}

pub fn default_dependency_graph(
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  build_plan: &BuildPlan,
) -> DependencyGraph {
  let imports = analysis
    .target
    .imports
    .iter()
    .map(|import| DependencyImport {
      id: format!(
        "import:{}:{}",
        sanitize_identifier(&import.library),
        sanitize_identifier(&import.name)
      ),
      library: import.library.clone(),
      symbol: import.name.clone(),
      status: "unresolved".to_string(),
      confidence: "medium".to_string(),
      evidence: vec![
        "observed from parsed import table".to_string(),
        format!("library={}", import.library),
        format!("symbol={}", import.name),
        "exact import library path, version, ordinal binding, and link invocation are unresolved"
          .to_string(),
      ],
    })
    .collect::<Vec<_>>();

  let exports = analysis
    .target
    .exports
    .iter()
    .map(|export| DependencyExport {
      id: format!("export:{}", sanitize_identifier(&export.name)),
      symbol: export.name.clone(),
      address: export.address,
      status: "observed".to_string(),
      evidence: vec![
        "observed from parsed export table".to_string(),
        format!("symbol={}", export.name),
        "export address is metadata evidence, not recovered source logic".to_string(),
      ],
    })
    .collect::<Vec<_>>();

  let relocation_edges = analysis
    .target
    .relocations
    .iter()
    .enumerate()
    .map(|(index, relocation)| DependencyRelocationEdge {
      id: format!(
        "relocation:{}:{}:{}",
        sanitize_identifier(&relocation.section),
        relocation.offset,
        index
      ),
      section: relocation.section.clone(),
      offset: relocation.offset,
      target: relocation.target.clone(),
      kind: relocation.kind.clone(),
      status: "observed-unresolved".to_string(),
      evidence: vec![
        format!("section={}", relocation.section),
        format!("kind={}", relocation.kind),
        format!("encoding={}", relocation.encoding),
        format!("target={}", relocation.target),
        "relocation target is evidence; source-level reference ownership remains unresolved"
          .to_string(),
      ],
    })
    .collect::<Vec<_>>();

  let runtime_artifacts = build_plan
    .link_plan
    .runtime_artifacts
    .iter()
    .map(|artifact| DependencyRuntimeArtifact {
      id: format!(
        "runtime:{}:{}",
        sanitize_identifier(&artifact.kind),
        sanitize_identifier(&artifact.name)
      ),
      name: artifact.name.clone(),
      kind: artifact.kind.clone(),
      status: artifact.status.clone(),
      evidence: artifact.evidence.clone(),
    })
    .collect::<Vec<_>>();

  let mut link_requirements = build_plan
    .link_plan
    .inputs
    .iter()
    .map(|input| DependencyLinkRequirement {
      id: format!("link-input:{}", sanitize_identifier(&input.name)),
      kind: input.kind.clone(),
      name: input.name.clone(),
      status: input.status.clone(),
      blockers: vec![
        "exact linker input path is unresolved".to_string(),
        "library version and search order are unresolved".to_string(),
        "Mizuchi will not fabricate import libraries, sysroots, or linker scripts".to_string(),
      ],
      evidence: input.evidence.clone(),
    })
    .collect::<Vec<_>>();

  let mut seen_requirement_ids = link_requirements
    .iter()
    .map(|requirement| requirement.id.clone())
    .collect::<BTreeSet<_>>();
  for dependency in &build_plan.dependencies {
    let id = format!(
      "dependency:{}:{}",
      sanitize_identifier(&dependency.library),
      sanitize_identifier(&dependency.symbol)
    );
    if seen_requirement_ids.insert(id.clone()) {
      link_requirements.push(DependencyLinkRequirement {
        id,
        kind: "import-symbol".to_string(),
        name: format!("{}!{}", dependency.library, dependency.symbol),
        status: dependency.status.clone(),
        blockers: vec![
          "exact dependency library path is unresolved".to_string(),
          "import symbol ownership and version are unresolved".to_string(),
        ],
        evidence: vec![
          "derived from build-plan dependency list".to_string(),
          format!("library={}", dependency.library),
          format!("symbol={}", dependency.symbol),
        ],
      });
    }
  }

  let unresolved_dependency_count = imports
    .iter()
    .filter(|import| import.status != "resolved")
    .count()
    + runtime_artifacts
      .iter()
      .filter(|artifact| artifact.status != "resolved")
      .count()
    + link_requirements
      .iter()
      .filter(|requirement| requirement.status != "resolved")
      .count();
  let status = if unresolved_dependency_count == 0 && (!imports.is_empty() || !runtime_artifacts.is_empty()) {
    "resolved"
  } else if imports.is_empty()
    && exports.is_empty()
    && relocation_edges.is_empty()
    && runtime_artifacts.is_empty()
    && link_requirements.is_empty()
  {
    "no-dependency-evidence"
  } else {
    "evidence-only"
  };

  DependencyGraph {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case.case_id.clone(),
    status: status.to_string(),
    import_count: imports.len(),
    export_count: exports.len(),
    relocation_edge_count: relocation_edges.len(),
    runtime_artifact_count: runtime_artifacts.len(),
    unresolved_dependency_count,
    imports,
    exports,
    relocation_edges,
    runtime_artifacts,
    link_requirements,
    uncertainty: vec![
      "Import tables, exports, relocations, and runtime artifacts are dependency evidence only."
        .to_string(),
      "Exact library paths, versions, search order, sysroots, import libraries, and linker scripts remain unresolved until proven by rebuild/diff evidence."
        .to_string(),
      "Address and offset facts may appear in dependency metadata but must not be emitted as recovered source logic."
        .to_string(),
    ],
  }
}

fn type_symbol_node(symbol: &TargetSymbol) -> TypeSymbolNode {
  let (demangle_status, demangled_name, namespace) = classify_symbol_name(&symbol.name);
  TypeSymbolNode {
    id: format!("symbol:{}", sanitize_identifier(&symbol.name)),
    name: symbol.name.clone(),
    kind: symbol.kind.clone(),
    scope: symbol.scope.clone(),
    address: symbol.address,
    size: symbol.size,
    demangle_status,
    demangled_name,
    namespace,
    evidence: vec![
      format!("symbol_kind={}", symbol.kind),
      format!("symbol_scope={}", symbol.scope),
      format!("symbol_size={}", symbol.size),
    ],
  }
}

fn classify_symbol_name(name: &str) -> (String, Option<String>, Vec<String>) {
  if name.starts_with("_Z") {
    return ("mangled-itanium-unresolved".to_string(), None, Vec::new());
  }
  if name.starts_with('?') {
    return ("mangled-msvc-unresolved".to_string(), None, Vec::new());
  }
  if name.starts_with("_R") {
    return ("mangled-rust-unresolved".to_string(), None, Vec::new());
  }
  if name.contains("::") {
    let namespace = name
      .split("::")
      .filter(|part| !part.is_empty())
      .map(ToString::to_string)
      .collect::<Vec<_>>();
    return ("already-readable".to_string(), Some(name.to_string()), namespace);
  }
  ("not-mangled-or-unknown".to_string(), None, Vec::new())
}

pub fn default_build_plan(
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  candidate_source: &str,
) -> BuildPlan {
  let compiler_known = analysis.target.toolchain.compiler != "unknown";
  let linker_known = analysis.target.toolchain.linker != "unknown";
  let proof_available = case.proof.target_object_path != "unavailable";

  let mut toolchain_evidence = analysis
    .target
    .toolchain
    .comment_strings
    .iter()
    .map(|item| format!("comment={item}"))
    .collect::<Vec<_>>();
  toolchain_evidence.extend(analysis.target.toolchain.notes.iter().cloned());
  toolchain_evidence.push(format!("file_kind={}", analysis.target.file_kind));
  toolchain_evidence.push(format!("architecture={}", analysis.target.architecture));
  let candidate_profiles = infer_compiler_profiles(analysis, &toolchain_evidence);
  let candidate_profile_ids = candidate_profiles
    .iter()
    .map(|profile| profile.id.clone())
    .collect::<Vec<_>>();
  let recommended_profile = recommend_compiler_profile(&candidate_profiles);
  let selected_profile = select_compiler_profile(&candidate_profiles);
  let build_system = infer_build_system(case, analysis, &candidate_profiles);
  let toolchain_stages = infer_toolchain_stages(&candidate_profiles, &toolchain_evidence);
  let link_inputs = infer_link_inputs(analysis, &candidate_profiles);
  let runtime_artifacts = infer_runtime_artifacts(analysis, &candidate_profiles);
  let is_archive = !analysis.target.archive_members.is_empty();

  let dependencies = analysis
    .target
    .imports
    .iter()
    .map(|import| BuildDependencyPlan {
      library: import.library.clone(),
      symbol: import.name.clone(),
      status: "unresolved".to_string(),
    })
    .collect::<Vec<_>>();

  let required_inputs = vec![
    BuildInputRequirement {
      name: "verified_source".to_string(),
      status: "missing".to_string(),
      detail: "No verified recovered source is available yet.".to_string(),
    },
    BuildInputRequirement {
      name: "compiler_configuration".to_string(),
      status: if compiler_known || linker_known {
        "partial".to_string()
      } else {
        "missing".to_string()
      },
      detail: if compiler_known || linker_known {
        format!(
          "Toolchain evidence exists (compiler={}, linker={}) but flags, versions, and invocation are unresolved.",
          analysis.target.toolchain.compiler, analysis.target.toolchain.linker
        )
      } else {
        "Compiler executable, linker executable, versions, flags, and invocation are unresolved."
          .to_string()
      },
    },
    BuildInputRequirement {
      name: "proof_target".to_string(),
      status: if proof_available {
        "available".to_string()
      } else {
        "missing".to_string()
      },
      detail: case.proof.target_object_path.clone(),
    },
    BuildInputRequirement {
      name: "linker_inputs".to_string(),
      status: if link_inputs.is_empty() {
        "partial".to_string()
      } else {
        "missing".to_string()
      },
      detail: if link_inputs.is_empty() {
        "No import-table linker inputs were observed; CRT/runtime ownership still needs proof."
          .to_string()
      } else {
        format!(
          "{} linker/runtime input(s) were observed but exact libraries, paths, and invocation remain unresolved.",
          link_inputs.len()
        )
      },
    },
  ];

  let mut blockers = vec![
    "No verified recovered source candidate exists.".to_string(),
    "A target-specific compiler configuration has not been fully recovered.".to_string(),
  ];
  if !proof_available {
    blockers.push("Object-level proof target is unavailable.".to_string());
  }
  if !dependencies.is_empty() {
    blockers.push("Imported dependency link inputs have not been resolved.".to_string());
  }
  if !runtime_artifacts.is_empty() {
    blockers.push("Runtime sidecars or startup artifacts remain unresolved.".to_string());
  }
  blockers.push("Mizuchi will not fabricate build flags, source logic, or linker inputs.".to_string());
  let build_units = if is_archive {
    analysis
      .target
      .archive_members
      .iter()
      .map(|member| BuildUnitPlan {
        id: member.id.clone(),
        source_path: format!("sources/candidates/{}.c", member.id),
        object_path: format!("build/members/{}.o", member.id),
        language: "c".to_string(),
        status: "blocked".to_string(),
        proof_target: "unavailable".to_string(),
        proof_target_status: "unavailable".to_string(),
        proof_target_locator: format!("archive-member:{}", member.name),
        proof_source_path: case.proof.target_object_path.clone(),
        proof_target_member_index: None,
        compiler_profile_candidates: candidate_profile_ids.clone(),
        dependency_symbols: Vec::new(),
        required_inputs: required_inputs.iter().map(|input| input.name.clone()).collect(),
        blockers: blockers
          .iter()
          .cloned()
          .chain([
            format!("archive_member={}", member.name),
            "Per-member proof targets and librarian invocation are unresolved.".to_string(),
          ])
          .collect(),
      })
      .collect()
  } else {
    vec![BuildUnitPlan {
      id: format!("{}_main", case.case_id),
      source_path: candidate_source.to_string(),
      object_path: "build/candidate.o".to_string(),
      language: "c".to_string(),
      status: "blocked".to_string(),
      proof_target: case.proof.target_object_path.clone(),
      proof_target_status: if case.proof.target_object_path == "unavailable" {
        "unavailable".to_string()
      } else {
        "configured".to_string()
      },
      proof_target_locator: "direct-object".to_string(),
      proof_source_path: case.proof.target_object_path.clone(),
      proof_target_member_index: None,
      compiler_profile_candidates: candidate_profile_ids.clone(),
      dependency_symbols: analysis
        .target
        .imports
        .iter()
        .map(|import| format!("{}!{}", import.library, import.name))
        .collect(),
      required_inputs: required_inputs.iter().map(|input| input.name.clone()).collect(),
      blockers: blockers.clone(),
    }]
  };
  let candidate_object = if is_archive {
    format!("build/lib{}.a", case.case_id)
  } else {
    "build/candidate.o".to_string()
  };
  let link_artifact_kind = if is_archive {
    "static-library"
  } else {
    "relocatable-object"
  };
  let candidate_source = if is_archive {
    analysis
      .target
      .archive_members
      .first()
      .map(|member| format!("sources/candidates/{}.c", member.id))
      .unwrap_or_else(|| candidate_source.to_string())
  } else {
    candidate_source.to_string()
  };

  BuildPlan {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case.case_id.clone(),
    state: "blocked".to_string(),
    compiler_script:
      "bash ./scripts/compile-placeholder.sh \"{{cFilePath}}\" \"{{objFilePath}}\""
        .to_string(),
    compiler_script_root: ".".to_string(),
    compiler_config_path: None,
    candidate_source,
    candidate_object: candidate_object.clone(),
    proof_target: case.proof.target_object_path.clone(),
    rebuild_supported: false,
    source_language: "c".to_string(),
    target_format: analysis.target.file_kind.clone(),
    target_architecture: analysis.target.architecture.clone(),
    expected_artifact: BuildArtifactSpec {
      path: candidate_object.clone(),
      kind: link_artifact_kind.to_string(),
      comparator: case.proof.comparator.clone(),
    },
    build_system,
    toolchain: BuildToolchainPlan {
      compiler: analysis.target.toolchain.compiler.clone(),
      linker: analysis.target.toolchain.linker.clone(),
      status: if compiler_known && linker_known {
        "partial".to_string()
      } else {
        "unknown".to_string()
      },
      evidence: toolchain_evidence,
      stages: toolchain_stages,
      ranking_status: if candidate_profiles.is_empty() {
        "unranked".to_string()
      } else {
        "evidence-scored".to_string()
      },
      recommended_profile,
      selected_profile,
      candidate_profiles,
    },
    link_plan: LinkPlan {
      artifact_path: candidate_object,
      kind: link_artifact_kind.to_string(),
      status: if link_inputs.is_empty() && runtime_artifacts.is_empty() {
        "partial".to_string()
      } else {
        "blocked".to_string()
      },
      linker_profile_candidates: candidate_profile_ids.clone(),
      inputs: link_inputs,
      runtime_artifacts,
      blockers: blockers.clone(),
    },
    build_units,
    dependencies,
    required_inputs,
    blockers,
  }
}

pub fn default_compiler_invocation_ledger(build_plan: &BuildPlan) -> CompilerInvocationLedger {
  let invocations = build_plan
    .build_units
    .iter()
    .flat_map(|unit| {
      build_plan
        .toolchain
        .candidate_profiles
        .iter()
        .filter(move |profile| unit.compiler_profile_candidates.contains(&profile.id))
        .map(move |profile| {
          let tool_candidates = profile
            .required_components
            .iter()
            .filter(|component| {
              matches!(component.kind.as_str(), "compiler" | "assembler" | "linker")
            })
            .map(|component| {
              let command_candidates = command_candidates_for_component(&component.name);
              let installed_candidates = probe_component_command_availability(&component.name);
              InvocationToolCandidate {
                component: component.name.clone(),
                kind: component.kind.clone(),
                command_candidates,
                installed_candidates,
              }
            })
            .collect::<Vec<_>>();
          let host_tool_available = tool_candidates.iter().all(|candidate| {
            candidate
              .installed_candidates
              .iter()
              .any(|command| command.installed)
          });
          let resolved_tool_candidates = tool_candidates
            .iter()
            .flat_map(|candidate| candidate.installed_candidates.iter())
            .filter(|candidate| candidate.installed)
            .count();
          let mut blockers = vec![
            "Exact compiler executable selection and version identity are unresolved.".to_string(),
            "Exact argument vector, optimization mode, ABI mode, include paths, and predefined macros are unresolved.".to_string(),
            "Environment variables, sysroot, CRT objects, libraries, and linker script are unresolved.".to_string(),
            "Invocation cannot be selected until rebuild and object/binary diff proof pass.".to_string(),
          ];
          if !host_tool_available {
            blockers.push(
              "One or more compiler/assembler/linker command families are missing on this host."
                .to_string(),
            );
          }
          if unit.proof_target_status != "mapped" {
            blockers.push(format!(
              "Golden proof target is not mapped for this build unit (status={}).",
              unit.proof_target_status
            ));
          }
          CompilerInvocationCandidate {
            id: format!("{}__{}", unit.id, profile.id),
            profile_id: profile.id.clone(),
            build_unit_id: unit.id.clone(),
            language: unit.language.clone(),
            status: "unresolved".to_string(),
            exact_command_recovered: false,
            source_path: unit.source_path.clone(),
            object_path: unit.object_path.clone(),
            proof_target: unit.proof_target.clone(),
            tool_candidates,
            argument_vector: Vec::new(),
            environment: vec![
              InvocationEnvironmentRequirement {
                name: "working_directory".to_string(),
                status: "unresolved".to_string(),
                detail: "Exact compiler working directory has not been recovered.".to_string(),
              },
              InvocationEnvironmentRequirement {
                name: "environment_variables".to_string(),
                status: "unresolved".to_string(),
                detail: "Target build environment variables have not been recovered.".to_string(),
              },
              InvocationEnvironmentRequirement {
                name: "filesystem_layout".to_string(),
                status: "unresolved".to_string(),
                detail: "Include, library, sysroot, and generated-file layout remains unresolved."
                  .to_string(),
              },
            ],
            required_evidence: vec![
              "compiler_executable_path".to_string(),
              "compiler_version_identity".to_string(),
              "argument_vector".to_string(),
              "optimization_and_codegen_flags".to_string(),
              "abi_and_calling_convention_mode".to_string(),
              "include_paths_and_predefined_macros".to_string(),
              "assembler_and_linker_inputs".to_string(),
              "runtime_startup_objects_and_libraries".to_string(),
              "rebuild_proof".to_string(),
              "object_or_binary_diff_proof".to_string(),
            ],
            blockers,
            evidence: vec![
              format!("profile_id={}", profile.id),
              format!("profile_score={}", profile.evidence_score),
              format!("profile_confidence={}", profile.evidence_confidence),
              format!("build_unit={}", unit.id),
              format!("source_path={}", unit.source_path),
              format!("object_path={}", unit.object_path),
              format!("proof_target={}", unit.proof_target),
              format!("proof_target_status={}", unit.proof_target_status),
              format!("proof_target_locator={}", unit.proof_target_locator),
              format!("proof_source_path={}", unit.proof_source_path),
              format!("host_tool_available={host_tool_available}"),
              format!("compiler_script_root={}", build_plan.compiler_script_root),
              format!(
                "compiler_script_configured={}",
                build_plan.rebuild_supported
              ),
              format!("resolved_tool_candidates={resolved_tool_candidates}"),
            ],
          }
        })
    })
    .collect::<Vec<_>>();
  let recovered_invocation_count = invocations
    .iter()
    .filter(|invocation| invocation.exact_command_recovered)
    .count();
  let status = if recovered_invocation_count > 0 {
    "partial"
  } else {
    "unresolved"
  };

  CompilerInvocationLedger {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: build_plan.case_id.clone(),
    status: status.to_string(),
    candidate_count: invocations.len(),
    recovered_invocation_count,
    missing_evidence: vec![
      "exact compiler/linker executable path".to_string(),
      "exact compiler/linker version and target mode".to_string(),
      "exact argument vector and response files".to_string(),
      "exact include paths, defines, sysroot, libraries, and startup objects".to_string(),
      "passing rebuild proof and authoritative diff proof".to_string(),
    ],
    invocations,
  }
}

fn inferred_profile_ids(analysis: &AnalysisRecord) -> Vec<String> {
  let shared = vec![
    format!("file_kind={}", analysis.target.file_kind),
    format!("architecture={}", analysis.target.architecture),
    format!("compiler={}", analysis.target.toolchain.compiler),
    format!("linker={}", analysis.target.toolchain.linker),
  ];
  infer_compiler_profiles(analysis, &shared)
    .into_iter()
    .map(|profile| profile.id)
    .collect()
}

fn infer_build_system(
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  profiles: &[CompilerProfile],
) -> BuildSystemPlan {
  let file_kind = analysis.target.file_kind.to_ascii_lowercase();
  let platform = case.target.platform.to_ascii_lowercase();
  let profile_ids = profiles
    .iter()
    .map(|profile| profile.id.as_str())
    .collect::<Vec<_>>();
  let mut candidate_backends = Vec::new();
  let mut generated_artifacts = Vec::new();

  fn push_backend(
    candidate_backends: &mut Vec<BuildBackendPlan>,
    generated_artifacts: &mut Vec<GeneratedBuildArtifact>,
    id: &str,
    family: &str,
    generator: &str,
    evidence: Vec<String>,
    artifact_path: &str,
  ) {
    candidate_backends.push(BuildBackendPlan {
      id: id.to_string(),
      family: family.to_string(),
      generator: generator.to_string(),
      status: "candidate".to_string(),
      evidence,
      blockers: vec![
        "Exact command lines, dependency edges, and environment variables are unresolved."
          .to_string(),
        "This backend remains non-executable until compiler, linker, flags, and library paths are proven."
          .to_string(),
      ],
    });
    generated_artifacts.push(GeneratedBuildArtifact {
      path: artifact_path.to_string(),
      kind: "generated-build-backend".to_string(),
      backend: id.to_string(),
      executable: false,
      detail: "Evidence-derived backend sketch; comments and metadata only.".to_string(),
    });
  }

  if file_kind.contains("elf") || platform == "unix" || platform == "ps2" {
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "make",
      "GNU Make",
      "makefile-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        format!("platform={}", case.target.platform),
        format!("compiler_profiles={}", profile_ids.join(",")),
      ],
      "build-system/Makefile.generated",
    );
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "ninja",
      "Ninja",
      "ninja-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        format!("platform={}", case.target.platform),
        "objdiff.custom_make_supports_alternate_build_drivers".to_string(),
      ],
      "build-system/build.ninja.generated",
    );
  }

  if file_kind.contains("pe") || file_kind.contains("coff") || platform == "windows" {
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "msbuild",
      "MSBuild",
      "msbuild-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        format!("platform={}", case.target.platform),
        format!("compiler_profiles={}", profile_ids.join(",")),
      ],
      "build-system/mizuchi.generated.props",
    );
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "nmake",
      "NMake",
      "nmake-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        "windows_native_compile_driver_candidate".to_string(),
      ],
      "build-system/Makefile.nmake.generated",
    );
  }

  if file_kind.contains("macho") || platform == "macos" {
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "xcodebuild",
      "Xcode Build",
      "xcconfig-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        format!("platform={}", case.target.platform),
        "apple_sdk_and_framework_resolution_required".to_string(),
      ],
      "build-system/mizuchi.generated.xcconfig",
    );
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "ninja",
      "Ninja",
      "ninja-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        "llvm_or_apple_clang_driver_candidate".to_string(),
      ],
      "build-system/build.ninja.generated",
    );
  }

  if candidate_backends.is_empty() {
    push_backend(
      &mut candidate_backends,
      &mut generated_artifacts,
      "manual-proof-driver",
      "Manual Proof Driver",
      "metadata-generated",
      vec![
        format!("file_kind={}", analysis.target.file_kind),
        "no_backend_family_could_be_inferred".to_string(),
      ],
      "build-system/backend.generated.txt",
    );
  }

  BuildSystemPlan {
    kind: "evidence-derived-manifest".to_string(),
    executable: false,
    reason:
      "Exact compiler commands, flags, linker scripts, environment variables, and library paths are unresolved."
        .to_string(),
    preferred_backend: candidate_backends.first().map(|backend| backend.id.clone()),
    candidate_backends,
    generated_artifacts,
  }
}

fn infer_toolchain_stages(
  profiles: &[CompilerProfile],
  evidence: &[String],
) -> Vec<ToolchainStagePlan> {
  let stage_specs = [
    ("compile", "Compile", "compiler"),
    ("assemble", "Assemble", "assembler"),
    ("link", "Link", "linker"),
    ("runtime", "Runtime", "runtime"),
    ("configure", "Configure", "configuration"),
  ];

  stage_specs
    .iter()
    .filter_map(|(id, name, kind)| {
      let required_components = profiles
        .iter()
        .flat_map(|profile| {
          profile
            .required_components
            .iter()
            .filter(move |component| component.kind == *kind)
            .map(|component| component.name.clone())
        })
        .collect::<Vec<_>>();
      if required_components.is_empty() {
        return None;
      }
      Some(ToolchainStagePlan {
        id: (*id).to_string(),
        name: (*name).to_string(),
        kind: (*kind).to_string(),
        status: "unresolved".to_string(),
        candidate_profiles: profiles.iter().map(|profile| profile.id.clone()).collect(),
        required_components,
        evidence: evidence.to_vec(),
        blockers: vec![
          "Exact executable path, version, mode, and flags are unresolved.".to_string(),
          "This stage is not selected until rebuild proof confirms a compiler profile.".to_string(),
        ],
      })
    })
    .collect()
}

fn infer_link_inputs(
  analysis: &AnalysisRecord,
  profiles: &[CompilerProfile],
) -> Vec<LinkInputPlan> {
  let mut inputs = analysis
    .target
    .imports
    .iter()
    .map(|import| LinkInputPlan {
      name: format!("{}!{}", import.library, import.name),
      kind: "import-symbol".to_string(),
      source: "import-table".to_string(),
      status: "unresolved".to_string(),
      evidence: vec![
        format!("library={}", import.library),
        format!("symbol={}", import.name),
      ],
    })
    .collect::<Vec<_>>();

  for component in profiles.iter().flat_map(|profile| {
    profile
      .required_components
      .iter()
      .filter(|component| component.kind == "runtime")
      .map(|component| component.name.clone())
  }) {
    inputs.push(LinkInputPlan {
      name: component.clone(),
      kind: "runtime-component".to_string(),
      source: "compiler-profile".to_string(),
      status: "unresolved".to_string(),
      evidence: vec![format!("component={component}")],
    });
  }

  inputs
}

fn infer_runtime_artifacts(
  analysis: &AnalysisRecord,
  profiles: &[CompilerProfile],
) -> Vec<RuntimeArtifactPlan> {
  let mut artifacts = Vec::new();

  if let Some(link) = &analysis.target.debug.gnu_debuglink {
    artifacts.push(RuntimeArtifactPlan {
      name: link.file.clone(),
      kind: "gnu-debuglink".to_string(),
      status: "unresolved".to_string(),
      detail: "Separate debug sidecar observed in the input binary.".to_string(),
      evidence: vec![format!("crc={}", link.crc)],
    });
  }
  if let Some(link) = &analysis.target.debug.gnu_debugaltlink {
    artifacts.push(RuntimeArtifactPlan {
      name: link.file.clone(),
      kind: "gnu-debugaltlink".to_string(),
      status: "unresolved".to_string(),
      detail: "Alternate debug sidecar observed in the input binary.".to_string(),
      evidence: vec![format!("build_id={}", link.build_id)],
    });
  }
  if let Some(uuid) = &analysis.target.debug.mach_uuid {
    artifacts.push(RuntimeArtifactPlan {
      name: uuid.clone(),
      kind: "mach-uuid".to_string(),
      status: "observed".to_string(),
      detail: "Mach-O UUID captured for debug/runtime correlation.".to_string(),
      evidence: vec![format!("uuid={uuid}")],
    });
  }

  for component in profiles.iter().flat_map(|profile| {
    profile
      .required_components
      .iter()
      .filter(|component| component.kind == "runtime")
      .map(|component| component.name.clone())
  }) {
    artifacts.push(RuntimeArtifactPlan {
      name: component.clone(),
      kind: "runtime-component".to_string(),
      status: "unresolved".to_string(),
      detail: "Runtime or startup component required by a candidate compiler profile.".to_string(),
      evidence: vec![format!("component={component}")],
    });
  }

  artifacts
}

fn infer_compiler_profiles(
  analysis: &AnalysisRecord,
  shared_evidence: &[String],
) -> Vec<CompilerProfile> {
  let compiler = analysis.target.toolchain.compiler.to_ascii_lowercase();
  let evidence_blob = shared_evidence
    .iter()
    .map(|item| item.to_ascii_lowercase())
    .collect::<Vec<_>>()
    .join("\n");
  let format = analysis
    .target
    .platform_fingerprint
    .object_format
    .to_ascii_lowercase();
  let arch = analysis.target.architecture.to_ascii_lowercase();
  let environment = analysis
    .target
    .platform_fingerprint
    .environment
    .to_ascii_lowercase();
  let triple_blob = analysis
    .target
    .platform_fingerprint
    .triple_candidates
    .join("\n")
    .to_ascii_lowercase();
  let mut profiles = Vec::new();

  if compiler.contains("gcc") || evidence_blob.contains("gcc") {
    profiles.push(compiler_profile(
      analysis,
      "gcc",
      "GCC",
      "GNU",
      "observed",
      shared_evidence,
      &["cc1", "as", "ld", "crt", "libgcc", "flags", "abi"],
    ));
  }
  if compiler.contains("clang") || evidence_blob.contains("clang") {
    profiles.push(compiler_profile(
      analysis,
      "clang",
      "Clang/LLVM",
      "LLVM",
      "observed",
      shared_evidence,
      &["clang", "llvm-mc", "lld-or-platform-linker", "crt", "compiler-rt", "flags", "abi"],
    ));
  }
  if compiler.contains("msvc")
    || evidence_blob.contains("msvc")
    || evidence_blob.contains("microsoft")
  {
    profiles.push(compiler_profile(
      analysis,
      "msvc",
      "MSVC",
      "Microsoft",
      "observed",
      shared_evidence,
      &["cl", "link", "ml-or-ml64", "msvcrt", "pdb", "flags", "abi"],
    ));
  }
  if evidence_blob.contains("rustc") {
    profiles.push(compiler_profile(
      analysis,
      "rustc",
      "rustc",
      "Rust",
      "observed",
      shared_evidence,
      &["rustc", "llvm-backend", "linker", "std-or-core", "flags", "abi"],
    ));
  }

  if profiles.is_empty() && format.contains("elf") {
    profiles.push(compiler_profile(
      analysis,
      "gcc-elf",
      "GCC",
      "GNU",
      "candidate",
      shared_evidence,
      &["compiler-driver", "assembler", "linker", "crt", "libraries", "flags", "abi"],
    ));
    profiles.push(compiler_profile(
      analysis,
      "clang-elf",
      "Clang/LLVM",
      "LLVM",
      "candidate",
      shared_evidence,
      &["clang", "llvm-mc", "linker", "crt", "compiler-rt", "libraries", "flags", "abi"],
    ));
  }
  if profiles.is_empty() && (format.contains("coff") || format.contains("pe")) {
    if environment == "msvc" || triple_blob.contains("windows-msvc") {
      profiles.push(compiler_profile(
        analysis,
        "msvc-pe",
        "MSVC",
        "Microsoft",
        "candidate",
        shared_evidence,
        &["cl", "link", "ml-or-ml64", "msvcrt", "pdb", "flags", "abi"],
      ));
      profiles.push(compiler_profile(
        analysis,
        "clang-cl-pe",
        "clang-cl",
        "LLVM-or-Microsoft",
        "candidate",
        shared_evidence,
        &["clang-cl", "link-or-lld-link", "msvcrt", "pdb", "flags", "abi"],
      ));
    } else if environment == "gnu" || triple_blob.contains("windows-gnu") {
      profiles.push(compiler_profile(
        analysis,
        "mingw-gcc-pe",
        "MinGW GCC",
        "GNU",
        "candidate",
        shared_evidence,
        &["compiler-driver", "assembler", "linker", "crt", "import-libraries", "flags", "abi"],
      ));
      profiles.push(compiler_profile(
        analysis,
        "clang-cl-pe",
        "clang-cl",
        "LLVM-or-Microsoft",
        "candidate",
        shared_evidence,
        &["clang-cl", "link-or-lld-link", "msvcrt", "pdb", "flags", "abi"],
      ));
    } else {
    profiles.push(compiler_profile(
      analysis,
      "msvc-pe",
      "MSVC",
      "Microsoft",
      "candidate",
      shared_evidence,
      &["cl", "link", "ml-or-ml64", "msvcrt", "pdb", "flags", "abi"],
    ));
    profiles.push(compiler_profile(
      analysis,
      "clang-cl-pe",
      "clang-cl",
      "LLVM-or-Microsoft",
      "candidate",
      shared_evidence,
      &["clang-cl", "link-or-lld-link", "msvcrt", "pdb", "flags", "abi"],
    ));
    profiles.push(compiler_profile(
      analysis,
      "mingw-gcc-pe",
      "MinGW GCC",
      "GNU",
      "candidate",
      shared_evidence,
      &["compiler-driver", "assembler", "linker", "crt", "import-libraries", "flags", "abi"],
    ));
    }
  }
  if profiles.is_empty() && format.contains("macho") {
    profiles.push(compiler_profile(
      analysis,
      "apple-clang-macho",
      "Apple Clang",
      "Apple",
      "candidate",
      shared_evidence,
      &["clang", "assembler", "ld64-compatible-linker", "sdk", "frameworks", "flags", "abi"],
    ));
    profiles.push(compiler_profile(
      analysis,
      "llvm-clang-macho",
      "Clang/LLVM",
      "LLVM",
      "candidate",
      shared_evidence,
      &["clang", "assembler", "ld64-compatible-linker", "sdk", "frameworks", "flags", "abi"],
    ));
  }
  if arch.contains("mips") {
    profiles.push(compiler_profile(
      analysis,
      "gcc-mips-elf",
      "GCC",
      "GNU",
      "candidate",
      shared_evidence,
      &["gcc", "assembler", "linker", "crt", "libraries", "flags", "abi"],
    ));
    profiles.push(compiler_profile(
      analysis,
      "ido-mips",
      "IDO",
      "Silicon Graphics",
      "candidate",
      shared_evidence,
      &["cc", "cfe", "uopt-or-ugen", "as1", "ld", "crt", "flags", "abi"],
    ));
  }
  if profiles.is_empty() {
    profiles.push(compiler_profile(
      analysis,
      "unknown-c-family",
      "unknown",
      "unknown",
      "unknown",
      shared_evidence,
      &["compiler", "assembler", "linker", "runtime", "libraries", "flags", "abi"],
    ));
  }

  profiles.sort_by(|left, right| {
    right
      .evidence_score
      .cmp(&left.evidence_score)
      .then_with(|| left.id.cmp(&right.id))
  });
  profiles
}

fn compiler_profile(
  analysis: &AnalysisRecord,
  id: &str,
  family: &str,
  vendor: &str,
  status: &str,
  evidence: &[String],
  components: &[&str],
) -> CompilerProfile {
  let (evidence_score, ranking_reasons) = score_compiler_profile(analysis, id, family, vendor, status);
  CompilerProfile {
    id: id.to_string(),
    family: family.to_string(),
    vendor: vendor.to_string(),
    role: "rebuild-candidate".to_string(),
    status: status.to_string(),
    evidence_score,
    evidence_confidence: evidence_confidence_label(evidence_score).to_string(),
    evidence: evidence.to_vec(),
    ranking_reasons,
    upstream_evidence: upstream_source_evidence(analysis, id),
    required_components: components
      .iter()
      .map(|component| CompilerComponentRequirement {
        name: (*component).to_string(),
        kind: classify_compiler_component(component).to_string(),
        status: "unresolved".to_string(),
        detail: "Required for byte-accurate rebuild; exact path, version, mode, and invocation are not recovered yet.".to_string(),
      })
      .collect(),
    uncertainty: vec![
      "Exact compiler version is unresolved.".to_string(),
      "Optimization flags, ABI mode, include paths, predefined macros, and linker script are unresolved.".to_string(),
      "This profile is not selected until recompilation and diff evidence prove it.".to_string(),
    ],
  }
}

fn score_compiler_profile(
  analysis: &AnalysisRecord,
  id: &str,
  family: &str,
  vendor: &str,
  status: &str,
) -> (u32, Vec<String>) {
  let format = analysis
    .target
    .platform_fingerprint
    .object_format
    .to_ascii_lowercase();
  let env = analysis
    .target
    .platform_fingerprint
    .environment
    .to_ascii_lowercase();
  let platform_vendor = analysis
    .target
    .platform_fingerprint
    .vendor
    .to_ascii_lowercase();
  let operating_system = analysis
    .target
    .platform_fingerprint
    .operating_system
    .to_ascii_lowercase();
  let compiler = analysis.target.toolchain.compiler.to_ascii_lowercase();
  let linker = analysis.target.toolchain.linker.to_ascii_lowercase();
  let arch = analysis.target.architecture.to_ascii_lowercase();
  let family_lower = family.to_ascii_lowercase();
  let id_lower = id.to_ascii_lowercase();
  let vendor_lower = vendor.to_ascii_lowercase();

  let mut score = 0_u32;
  let mut reasons = Vec::new();

  match status {
    "observed" => {
      score += 60;
      reasons.push("profile status is observed from toolchain evidence".to_string());
    }
    "candidate" => {
      score += 20;
      reasons.push("profile is a format-compatible rebuild candidate".to_string());
    }
    _ => {
      score += 5;
      reasons.push("profile remains a low-confidence fallback".to_string());
    }
  }

  let format_match = (format == "elf" && (id_lower.contains("elf") || id_lower == "gcc" || id_lower == "clang" || id_lower.contains("mips")))
    || ((format == "coff" || format == "pe")
      && (id_lower.contains("pe") || id_lower.contains("msvc") || id_lower.contains("mingw") || id_lower.contains("clang-cl")))
    || (format == "macho" && (id_lower.contains("macho") || vendor_lower == "apple"));
  if format_match {
    score += 20;
    reasons.push(format!("profile matches target object format `{format}`"));
  }

  if !compiler.is_empty() && compiler != "unknown" {
    if compiler.contains(&id_lower)
      || compiler.contains(&family_lower)
      || (id_lower.contains("clang-cl") && compiler.contains("clang"))
      || (id_lower.contains("mingw") && compiler.contains("gcc"))
      || (id_lower.contains("msvc") && compiler.contains("msvc"))
    {
      score += 30;
      reasons.push(format!("compiler evidence references `{}`", analysis.target.toolchain.compiler));
    }
  }

  if !linker.is_empty()
    && linker != "unknown"
    && ((id_lower.contains("msvc") && linker.contains("link"))
      || (id_lower.contains("mingw") && linker.contains("ld"))
      || ((id_lower.contains("clang") || id_lower == "gcc") && (linker.contains("ld") || linker.contains("lld"))))
  {
    score += 10;
    reasons.push(format!("linker evidence is compatible with `{}`", analysis.target.toolchain.linker));
  }

  if (id_lower.contains("msvc") || id_lower.contains("clang-cl")) && env == "msvc" {
    score += 15;
    reasons.push("target environment indicates MSVC-family ABI/runtime".to_string());
  }
  if id_lower.contains("mingw") && env == "gnu" {
    score += 15;
    reasons.push("target environment indicates GNU/MinGW runtime family".to_string());
  }
  if (id_lower == "gcc-elf" || id_lower == "clang-elf" || id_lower == "gcc" || id_lower == "clang")
    && (env == "gnu" || env == "unknown")
    && format == "elf"
  {
    score += 10;
    reasons.push("ELF target is compatible with generic GCC/Clang rebuild families".to_string());
  }
  if (id_lower.contains("macho") || vendor_lower == "apple")
    && (operating_system == "macos" || platform_vendor == "apple")
  {
    score += 15;
    reasons.push("target vendor/platform indicates Apple toolchain expectations".to_string());
  }

  if arch.contains("mips") && id_lower.contains("mips") {
    score += 10;
    reasons.push("architecture-specific MIPS profile matches target architecture".to_string());
  }
  if arch.contains("x86_64") && (id_lower.contains("msvc") || id_lower.contains("mingw") || id_lower.contains("clang-cl")) && (format == "pe" || format == "coff") {
    score += 5;
    reasons.push("x86_64 PE/COFF target is compatible with Windows toolchain families".to_string());
  }

  if reasons.is_empty() {
    reasons.push("no strong compiler-family evidence was observed".to_string());
  }

  (score.min(100), reasons)
}

fn evidence_confidence_label(score: u32) -> &'static str {
  if score >= 85 {
    "high"
  } else if score >= 55 {
    "medium"
  } else {
    "low"
  }
}

fn recommend_compiler_profile(profiles: &[CompilerProfile]) -> Option<String> {
  profiles
    .iter()
    .find(|profile| profile.evidence_score >= 35)
    .map(|profile| profile.id.clone())
}

fn select_compiler_profile(profiles: &[CompilerProfile]) -> Option<String> {
  let best = profiles.first()?;
  let next_score = profiles.get(1).map(|profile| profile.evidence_score).unwrap_or(0);
  ((best.status == "observed" || best.evidence_confidence == "high")
    && best.evidence_score >= 85
    && best.evidence_score.saturating_sub(next_score) >= 10)
    .then(|| best.id.clone())
}

fn upstream_source_evidence(
  analysis: &AnalysisRecord,
  profile_id: &str,
) -> Vec<UpstreamSourceEvidence> {
  let mut evidence = vec![
    gh_source_evidence(
      "objdiff",
      "proof-and-project-config",
      &[profile_id],
      "encounter/objdiff",
      "main",
      "config.schema.json",
      "3bd59c5ba433ebf742ad267f96c6b0e388738696",
      "Project-level object comparison and alternate build-driver modeling remain authoritative proof surfaces.",
      "integrate-via-config-contract",
    ),
    gh_source_evidence(
      "llvm",
      "object-intake-model",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "llvm/lib/Object/ObjectFile.cpp",
      "b0e4ea0a51ba1fb08a45ad5efb3a38b4ae373f24",
      "Object-file parsing is an evidence extraction boundary for sections, symbols, relocations, and debug locators; it is not recovered source.",
      "selective-port-via-rust-object-crate",
    ),
    gh_source_evidence(
      "llvm",
      "target-identity-model",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "llvm/include/llvm/TargetParser/Triple.h",
      "2fb28cfda053fdaece282b65314b73b15b15489b",
      "Architecture, vendor, operating system, environment, and object format must remain separate evidence dimensions.",
      "selective-port-only",
    ),
    gh_source_evidence(
      "retdec",
      "decompiler-adapter-boundary",
      &[profile_id],
      "avast/retdec",
      "master",
      "src/bin2llvmir/optimizations/main_detection/main_detection.cpp",
      "d1f8da34e0cbcdf21b18173e54b8b852a9101fe4",
      "RetDec-style analysis can inform entrypoint and decompiler-adapter evidence, but it is not a binary-equivalence proof source.",
      "adapter-evidence-only",
    ),
    gh_source_evidence(
      "ghidra",
      "decompiler-interface-boundary",
      &[profile_id],
      "NationalSecurityAgency/ghidra",
      "master",
      "Ghidra/Features/Decompiler/src/main/java/ghidra/app/decompiler/DecompInterface.java",
      "45fa1b9657347760918b2caf9e57bf2fd24fa93c",
      "Ghidra decompiler interfaces can provide typed analysis and pseudocode evidence, but they are not source recovery or binary-equivalence proof by themselves.",
      "adapter-evidence-only",
    ),
  ];

  if adapter_uses_provider(&analysis.adapter, "ghidra") {
    evidence.push(gh_source_evidence(
      "ghidra",
      "analysis-boundary",
      &[profile_id],
      "NationalSecurityAgency/ghidra",
      "master",
      "Ghidra/Features/Base/src/main/java/ghidra/app/util/headless/HeadlessAnalyzer.java",
      "a516aee929634ddde57e7f00add6f8d0aca12b22",
      "Headless import and scripted analysis are evidence sources, not rebuild proof.",
      "do-not-port-wholesale",
    ));
  }

  if profile_id == "gcc-elf" || profile_id == "clang-elf" || profile_id == "gcc" || profile_id == "clang" {
    evidence.push(gh_source_evidence(
      "llvm",
      "gnu-toolchain-compatibility",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "clang/lib/Driver/ToolChains/Gnu.cpp",
      "c489a1bddcb26a5ebccb705bbb792d9c387fbcb9",
      "Public GNU toolchain compatibility logic is useful for modeling ELF sysroot, startup objects, multilib, assembler, and linker-family expectations.",
      "model-toolchain-contract-only",
    ));
  }

  if profile_id.contains("gcc") {
    evidence.push(gh_source_evidence(
      "gcc",
      "gcc-driver-model",
      &[profile_id],
      "gcc-mirror/gcc",
      "master",
      "gcc/gcc.cc",
      "0ed2cd96be1521628165ecdd6258f5d199a2b84b",
      "GCC driver source informs public compiler-driver boundaries, but exact historical target behavior still requires recovered executable/version/flags and proof.",
      "model-toolchain-contract-only",
    ));
    evidence.push(gh_source_evidence(
      "gcc",
      "gcc-target-config-model",
      &[profile_id],
      "gcc-mirror/gcc",
      "master",
      "gcc/config.gcc",
      "fabd5f75f9655a3573fe89e947fc0839f5958aa8",
      "GCC target configuration source helps model target-family compatibility, not exact emitted code for the unknown target compiler.",
      "model-toolchain-contract-only",
    ));
  }

  if profile_id.contains("clang") || profile_id == "clang" {
    evidence.push(gh_source_evidence(
      "llvm",
      "driver-dispatch",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "clang/lib/Driver/Driver.cpp",
      "5cbf98fc074b66742f1f22e2495acc72e475c5e7",
      "Clang driver dispatch is useful for modeling public toolchain selection boundaries, not for fabricating exact compiler invocations.",
      "selective-port-only",
    ));
  }

  if profile_id == "msvc-pe" || profile_id == "clang-cl-pe" || profile_id == "msvc" {
    evidence.push(gh_source_evidence(
      "llvm",
      "windows-toolchain-compatibility",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "clang/lib/Driver/ToolChains/MSVC.cpp",
      "3c3bfe33b9f074340988c0a9d16342edfc07dad2",
      "Public clang/LLVM compatibility logic helps model MSVC-family runtime, linker, and driver expectations without pretending to implement proprietary MSVC internals.",
      "model-toolchain-contract-only",
    ));
  }

  if profile_id == "mingw-gcc-pe" {
    evidence.push(gh_source_evidence(
      "llvm",
      "windows-gnu-toolchain-compatibility",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "clang/lib/Driver/ToolChains/MinGW.cpp",
      "11dca2fa4231d8afe76bf315d56006260a706186",
      "Public MinGW toolchain modeling is useful for sysroot, runtime, and linker-family evidence on PE/COFF targets.",
      "model-toolchain-contract-only",
    ));
  }

  if profile_id == "apple-clang-macho" || profile_id == "llvm-clang-macho" {
    evidence.push(gh_source_evidence(
      "llvm",
      "darwin-toolchain-compatibility",
      &[profile_id],
      "llvm/llvm-project",
      "main",
      "clang/lib/Driver/ToolChains/Darwin.cpp",
      "59744d1cb3e8c1f6bc45b59297ac08ebc0c9cee0",
      "Darwin toolchain modeling is relevant for Mach-O arch normalization, SDK/runtime expectations, and linker startup objects.",
      "model-toolchain-contract-only",
    ));
  }

  if profile_id.contains("ido") {
    evidence.push(unresolved_source_evidence(
      "ido",
      "compiler-family-gap",
      &[profile_id],
      "Exact proprietary compiler stages must be fingerprinted from artifacts; Mizuchi does not fabricate an implementation.",
      "blocked-on-source-availability",
    ));
  }

  evidence
}

fn adapter_uses_provider(adapter: &AdapterDescriptor, provider_id: &str) -> bool {
  adapter
    .analysis_providers
    .iter()
    .any(|provider| provider.id == provider_id)
}

fn gh_source_evidence(
  system: &str,
  role: &str,
  profiles: &[&str],
  repo: &str,
  ref_name: &str,
  path: &str,
  revision: &str,
  rationale: &str,
  rust_port_status: &str,
) -> UpstreamSourceEvidence {
  UpstreamSourceEvidence {
    system: system.to_string(),
    role: role.to_string(),
    applies_to_profiles: profiles.iter().map(|profile| (*profile).to_string()).collect(),
    repo: repo.to_string(),
    path: path.to_string(),
    revision: revision.to_string(),
    source_kind: "github-content-file".to_string(),
    source_sha: revision.to_string(),
    api_url: format!("https://api.github.com/repos/{repo}/contents/{path}?ref={ref_name}"),
    git_url: format!("https://api.github.com/repos/{repo}/git/blobs/{revision}"),
    html_url: format!("https://github.com/{repo}/blob/{ref_name}/{path}"),
    download_url: format!("https://raw.githubusercontent.com/{repo}/{ref_name}/{path}"),
    verification: "catalog-reference".to_string(),
    rationale: rationale.to_string(),
    rust_port_status: rust_port_status.to_string(),
  }
}

fn unresolved_source_evidence(
  system: &str,
  role: &str,
  profiles: &[&str],
  rationale: &str,
  rust_port_status: &str,
) -> UpstreamSourceEvidence {
  UpstreamSourceEvidence {
    system: system.to_string(),
    role: role.to_string(),
    applies_to_profiles: profiles.iter().map(|profile| (*profile).to_string()).collect(),
    repo: "unresolved/proprietary".to_string(),
    path: "n/a".to_string(),
    revision: "unresolved".to_string(),
    source_kind: "unresolved".to_string(),
    source_sha: "unresolved".to_string(),
    api_url: "unavailable".to_string(),
    git_url: "unavailable".to_string(),
    html_url: "unavailable".to_string(),
    download_url: "unavailable".to_string(),
    verification: "source-unavailable".to_string(),
    rationale: rationale.to_string(),
    rust_port_status: rust_port_status.to_string(),
  }
}

fn classify_compiler_component(component: &str) -> &'static str {
  let lower = component.to_ascii_lowercase();
  if lower.contains("link") || lower == "ld" || lower.contains("ld64") {
    "linker"
  } else if lower.contains("as") || lower.contains("ml") || lower.contains("mc") {
    "assembler"
  } else if lower.contains("crt")
    || lower.contains("lib")
    || lower.contains("sdk")
    || lower.contains("framework")
    || lower.contains("std")
  {
    "runtime"
  } else if lower.contains("flag") || lower.contains("abi") || lower.contains("macro") {
    "configuration"
  } else {
    "compiler"
  }
}

pub fn default_uncertainty(case_id: &str, adapter: &AdapterDescriptor) -> UncertaintyLedger {
  let mut items = vec![UncertaintyItem::blocking(
    "semantic-recovery-pending",
    FailureClass::SemanticUnknown,
    "Recovered source logic is not available yet; the generated source candidate is a marked blocking stub.",
    vec![
      format!("adapter={}", adapter.id),
      "source_candidates are evidence placeholders only".to_string(),
    ],
  )];

  if !adapter.supports_recovery {
    items.push(UncertaintyItem::blocking(
      "adapter-recovery-unsupported",
      FailureClass::UnsupportedFormat,
      format!(
        "Adapter '{}' currently supports probing and reporting only.",
        adapter.id
      ),
      vec!["recovery=false".to_string()],
    ));
  }

  UncertaintyLedger {
    schema_version: 1,
    generated_at: now_rfc3339(),
    case_id: case_id.to_string(),
    items,
  }
}

pub fn blocked_source_candidate(case_id: &str) -> String {
  format!(
    "/*\n * Evidence-only source candidate generated by decomp.\n * Case: {case_id}\n * Status: incomplete\n * This file intentionally blocks compilation until verified recovery exists.\n */\n#error \"Recovery incomplete: no verified source candidate available yet.\"\n"
  )
}

fn extract_toolchain_evidence(file: &File<'_>) -> ToolchainEvidence {
  let comment_strings = file
    .section_by_name(".comment")
    .and_then(|section| section.data().ok())
    .map(split_metadata_strings)
    .unwrap_or_default();
  let notes = file
    .sections()
    .filter_map(|section| {
      let name = section.name().ok()?;
      if name.starts_with(".note") {
        Some(format!("section:{name}"))
      } else {
        None
      }
    })
    .collect::<Vec<_>>();

  let compiler = classify_toolchain_component(&comment_strings, &["gcc", "clang", "msvc", "rustc"]);
  let linker = classify_toolchain_component(&comment_strings, &["linker", "ld.", "lld", "gold"]);

  ToolchainEvidence {
    comment_strings,
    notes,
    compiler,
    linker,
  }
}

fn classify_toolchain_component(strings: &[String], needles: &[&str]) -> String {
  strings
    .iter()
    .find(|value| {
      let lower = value.to_ascii_lowercase();
      needles.iter().any(|needle| lower.contains(needle))
    })
    .cloned()
    .unwrap_or_else(|| "unknown".to_string())
}

fn split_metadata_strings(data: &[u8]) -> Vec<String> {
  data
    .split(|byte| *byte == 0 || *byte == b'\n')
    .map(bytes_to_string)
    .map(|value| value.trim().to_string())
    .filter(|value| !value.is_empty())
    .collect()
}

fn bytes_to_string(data: &[u8]) -> String {
  String::from_utf8_lossy(data).into_owned()
}

pub fn write_yaml<T: Serialize>(path: &Path, value: &T) -> Result<()> {
  let data = serde_yaml::to_string(value)?;
  fs::write(path, data)?;
  Ok(())
}

pub fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
  let data = serde_json::to_vec_pretty(value)?;
  fs::write(path, data)?;
  Ok(())
}

pub fn now_rfc3339() -> String {
  OffsetDateTime::now_utc()
    .format(&Rfc3339)
    .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_string())
}

pub fn command_exists(name: &str) -> bool {
  std::env::var_os("PATH")
    .map(|paths| {
      std::env::split_paths(&paths).any(|path| {
        let candidate = path.join(name);
        candidate.is_file()
      })
    })
    .unwrap_or(false)
}

pub fn sanitize_identifier(value: &str) -> String {
  let mut out = String::new();
  for ch in value.chars() {
    if ch.is_ascii_alphanumeric() {
      out.push(ch.to_ascii_lowercase());
    } else {
      out.push('_');
    }
  }
  let trimmed = out.trim_matches('_');
  if trimmed.is_empty() {
    "target".to_string()
  } else {
    trimmed.to_string()
  }
}
