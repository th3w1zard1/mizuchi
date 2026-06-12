use std::fs;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Command;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use anyhow::{anyhow, Context, Result};
use object::{read::archive::ArchiveFile, File, Object, ObjectSection, ObjectSymbol, RelocationTarget};
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::adapter::{probe_target, registered_adapter};
use crate::model::{
  ArtifactComparison, ArtifactFingerprint, ComparableRelocation, ComparableSection,
  ComparableSymbol,
  blocked_source_candidate, default_build_plan, default_case_manifest,
  default_cfg_evidence_graph, default_compiler_invocation_ledger, default_dependency_graph,
  default_reconstruction, default_type_relation_graph,
  default_uncertainty, derive_attempt_plan, command_candidates_for_component,
  command_exists_normalized, derive_proof_target_ledger, probe_component_command_availability,
  sanitize_identifier,
  write_json, write_yaml,
  ActionSummary, AnalysisRecord, BuildPlan, BuildUnitPlan, BuildUnitProofResult,
  CaseManifest,
  FailureClass, ProjectSnapshot, ReconstructionGraph, ReportFormat, RunRequest, SourceArtifactAudit,
  SourceAuditRecord, UncertaintyItem,
  VerificationCheck, VerificationRecord,
};
use crate::report;

pub fn run_request(request: &RunRequest, repo_root: &Path) -> Result<ProjectSnapshot> {
  let resolved = resolve_run_input(request, repo_root)?;
  let mut adapter_uncertainties = resolved.adapter_uncertainties;

  fs::create_dir_all(&request.project)
    .with_context(|| format!("failed to create {}", request.project.display()))?;
  fs::create_dir_all(request.project.join("build"))?;
  fs::create_dir_all(request.project.join("sources/candidates"))?;

  let case = resolved
    .case
    .unwrap_or_else(|| default_case_manifest(&resolved.target, &resolved.adapter));
  let mut analysis = AnalysisRecord::from_target(&resolved.target, &resolved.adapter, repo_root);
  analysis.case_id = case.case_id.clone();
  analysis.notes.extend(resolved.analysis_notes);
  let candidate_rel = format!("sources/candidates/{}.c", case.case_id);
  let mut reconstruction = default_reconstruction(&case, &analysis, &candidate_rel);
  let cfg_evidence = default_cfg_evidence_graph(&case, &analysis);
  let type_relations = default_type_relation_graph(&case, &analysis);
  let mut build_plan = default_build_plan(&case, &analysis, &candidate_rel);
  apply_proof_target_mapping(repo_root, &case, &analysis, &mut reconstruction, &mut build_plan);
  let imported_sources = discover_imported_source_candidates(
    &case,
    &analysis,
    &resolved.source_search_roots,
    repo_root,
  );
  apply_imported_source_candidates(
    &mut analysis,
    &mut reconstruction,
    &mut build_plan,
    &imported_sources,
  );
  let mut uncertainty = default_uncertainty(&case.case_id, &resolved.adapter);
  uncertainty.items.append(&mut adapter_uncertainties);
  let runtime_compiler =
    resolve_runtime_compiler_config(&resolved.config_search_roots, repo_root);
  apply_runtime_compiler_config(&mut build_plan, &runtime_compiler, &mut uncertainty);
  let dependency_graph = default_dependency_graph(&case, &analysis, &build_plan);
  let mut compiler_invocation = default_compiler_invocation_ledger(&build_plan);
  let mut verification = VerificationRecord::new(&case.case_id);
  apply_analysis_provider_availability(&analysis, &mut verification, &mut uncertainty);
  apply_cfg_evidence_graph(&cfg_evidence, &mut verification, &mut uncertainty);
  apply_type_relation_graph(&type_relations, &mut verification, &mut uncertainty);
  apply_dependency_graph(&dependency_graph, &mut verification, &mut uncertainty);

  verification.add_failure(FailureClass::SemanticUnknown);
  if !resolved.adapter.supports_recovery {
    verification.add_failure(FailureClass::UnsupportedFormat);
  }

  if request.match_requested {
    uncertainty.items.push(UncertaintyItem::blocking(
      "match-runner-pending",
      FailureClass::SemanticUnknown,
      "Deterministic rebuild/verify can run, but AI repair and iterative one-shot match orchestration are not implemented in the Rust slice yet.",
      vec!["match_requested=true".to_string()],
    ));
    verification.add_failure(FailureClass::SemanticUnknown);
  }

  materialize_source_candidates(
    &request.project,
    &case.case_id,
    &reconstruction,
    &imported_sources,
  )?;
  materialize_proof_targets(&request.project, &build_plan, &analysis, repo_root)?;
  let source_audit = audit_source_artifacts(&request.project, &case.case_id, &reconstruction)?;
  apply_source_audit(&source_audit, &mut verification, &mut uncertainty);

  if request.rebuild {
    run_rebuild(
      repo_root,
      &request.project,
      &build_plan,
      &analysis,
      &case.symbol.name,
      &mut verification,
      &mut uncertainty,
    )?;
  }

  if request.verify || request.match_requested {
    if !request.rebuild && build_plan.rebuild_supported {
      run_rebuild(
        repo_root,
        &request.project,
        &build_plan,
        &analysis,
        &case.symbol.name,
        &mut verification,
        &mut uncertainty,
      )?;
    }
    run_verify(
      repo_root,
      &request.project,
      &build_plan,
      &analysis,
      &mut verification,
      &mut uncertainty,
    )?;
  }

  recover_replayed_compiler_invocations(
    &request.project,
    &build_plan,
    &case.symbol.name,
    &verification,
    &mut compiler_invocation,
  );
  apply_compiler_invocation_ledger(&compiler_invocation, &mut verification, &mut uncertainty);

  verification.finalize();

  let snapshot = ProjectSnapshot {
    actions: ActionSummary::from_request(request),
    case,
    analysis,
    reconstruction,
    cfg_evidence,
    type_relations,
    dependency_graph,
    build_plan,
    compiler_invocation,
    source_audit,
    verification,
    uncertainty,
  };

  write_project(&request.project, &snapshot, repo_root)?;
  Ok(snapshot)
}

struct ResolvedRunInput {
  target: crate::model::TargetInput,
  adapter: crate::model::AdapterDescriptor,
  adapter_uncertainties: Vec<UncertaintyItem>,
  case: Option<CaseManifest>,
  analysis_notes: Vec<String>,
  config_search_roots: Vec<PathBuf>,
  source_search_roots: Vec<PathBuf>,
}

struct CaseArtifactCandidate {
  label: &'static str,
  raw_path: String,
}

#[derive(Debug, Clone)]
struct ImportedSourceCandidate {
  output_path: String,
  source_path: PathBuf,
  kind: &'static str,
  evidence: Vec<String>,
}

#[derive(Debug, Clone)]
struct RuntimeCompilerConfig {
  template: String,
  runtime_root: PathBuf,
  config_path: Option<PathBuf>,
  rebuild_supported: bool,
  uses_placeholder: bool,
  evidence: Vec<String>,
  uncertainties: Vec<UncertaintyItem>,
}

#[derive(Debug, Clone)]
struct UpstreamEvidenceCatalogValidation {
  mode: String,
  gh_available: bool,
  catalog_status: String,
  validated_reference_count: usize,
  matched_reference_count: usize,
  drifted_reference_count: usize,
  missing_reference_count: usize,
  error_count: usize,
  entries: Vec<UpstreamEvidenceValidationEntry>,
  evidence: Vec<String>,
}

#[derive(Debug, Clone)]
struct UpstreamEvidenceValidationEntry {
  status: String,
  matched_catalog_source: Option<bool>,
  resolved_source_sha: Option<String>,
  resolved_html_url: Option<String>,
  resolved_download_url: Option<String>,
  evidence: Vec<String>,
}

#[derive(Debug, serde::Deserialize)]
struct GhContentsResponse {
  sha: Option<String>,
  path: Option<String>,
  #[serde(rename = "html_url")]
  html_url: Option<String>,
  #[serde(rename = "download_url")]
  download_url: Option<String>,
}

fn resolve_run_input(request: &RunRequest, repo_root: &Path) -> Result<ResolvedRunInput> {
  if request.target.is_dir() {
    let case_path = request.target.join("case.yaml");
    if case_path.is_file() {
      return resolve_case_input(&case_path, repo_root);
    }
    return Err(anyhow!(
      "target directory {} does not contain case.yaml",
      request.target.display()
    ));
  }

  if request
    .target
    .file_name()
    .and_then(|name| name.to_str())
    .map(|name| name.eq_ignore_ascii_case("case.yaml"))
    .unwrap_or(false)
  {
    return resolve_case_input(&request.target, repo_root);
  }

  let (target, adapter, adapter_uncertainties) = probe_target(&request.target)?;
  Ok(ResolvedRunInput {
    config_search_roots: request
      .target
      .parent()
      .map(|parent| vec![parent.to_path_buf()])
      .unwrap_or_default(),
    source_search_roots: Vec::new(),
    target,
    adapter,
    adapter_uncertainties,
    case: None,
    analysis_notes: Vec::new(),
  })
}

fn resolve_case_input(case_path: &Path, repo_root: &Path) -> Result<ResolvedRunInput> {
  let case_dir = case_path.parent().unwrap_or(repo_root);
  let mut case: CaseManifest = serde_yaml::from_str(
    &fs::read_to_string(case_path)
      .with_context(|| format!("failed to read case manifest {}", case_path.display()))?,
  )
  .with_context(|| format!("failed to parse case manifest {}", case_path.display()))?;

  let mut adapter = registered_adapter(&case.adapter.id).ok_or_else(|| {
    anyhow!(
      "case adapter {} is not registered in the Rust runtime",
      case.adapter.id
    )
  })?;
  if !case.adapter.capabilities_profile.is_empty() {
    adapter.capabilities_profile = case.adapter.capabilities_profile.clone();
  }
  if !case.ingest.source_type.is_empty() {
    adapter.source_type = case.ingest.source_type.clone();
  }
  if !case.target.family.is_empty() {
    adapter.family = case.target.family.clone();
  }
  if !case.target.platform.is_empty() {
    adapter.platform = case.target.platform.clone();
  }
  if !case.load.tool.is_empty() {
    adapter.load_tool = case.load.tool.clone();
  }
  if !case.load.analysis_providers.is_empty() {
    adapter.analysis_providers = case.load.analysis_providers.clone();
  }
  if !case.load.context_path.is_empty() {
    adapter.context_path = case.load.context_path.clone();
  }

  let prefer_proof_target = case.proof.comparator == "objdiff" || case.proof.source == "golden-object";
  let mut candidates = Vec::new();
  if prefer_proof_target {
    candidates.push(CaseArtifactCandidate {
      label: "proof-target",
      raw_path: case.proof.target_object_path.clone(),
    });
  }
  candidates.extend([
    CaseArtifactCandidate {
      label: "program-path",
      raw_path: case.load.program_path.clone(),
    },
    CaseArtifactCandidate {
      label: "target-binary",
      raw_path: case.target.binary.clone(),
    },
    CaseArtifactCandidate {
      label: "ingest-source",
      raw_path: case.ingest.source_path.clone(),
    },
  ]);
  if !prefer_proof_target {
    candidates.push(CaseArtifactCandidate {
      label: "proof-target",
      raw_path: case.proof.target_object_path.clone(),
    });
  }

  let mut attempted_paths = Vec::new();
  let mut selected = None;
  for candidate in &candidates {
    if let Some(path) = resolve_case_artifact_path(case_dir, repo_root, &candidate.raw_path) {
      attempted_paths.push(format!("{}={}", candidate.label, path.display()));
      if path.is_file() {
        selected = Some((candidate.label, candidate.raw_path.clone(), path));
        break;
      }
    }
  }

  let Some((selected_label, _selected_raw, selected_path)) = selected else {
    return Err(anyhow!(
      "case manifest {} did not resolve to a local analyzable artifact; tried: {}",
      case_path.display(),
      attempted_paths.join(", ")
    ));
  };

  let (target, detected_adapter, _detected_uncertainties) = probe_target(&selected_path)?;
  let mut analysis_notes = vec![
    format!("intake_case_manifest={}", case_path.display()),
    format!("analysis_artifact_kind={selected_label}"),
    format!("analysis_artifact_path={}", selected_path.display()),
  ];
  if selected_label == "proof-target" {
    analysis_notes.push(
      "analysis uses the local proof target object because it is the strongest available exact-match artifact for this imported case"
        .to_string(),
    );
  }
  if detected_adapter.id != adapter.id {
    analysis_notes.push(format!(
      "detected_artifact_adapter={} while imported_case_adapter={} was preserved",
      detected_adapter.id, adapter.id
    ));
  }

  case.proof.target_object_path =
    normalize_case_runtime_path(case_dir, repo_root, &case.proof.target_object_path);
  case.workspace.prompt_path = ".".to_string();
  case.workspace.build_dir = "build".to_string();

  Ok(ResolvedRunInput {
    config_search_roots: vec![case_dir.to_path_buf()],
    source_search_roots: vec![case_dir.to_path_buf()],
    target,
    adapter,
    adapter_uncertainties: Vec::new(),
    case: Some(case),
    analysis_notes,
  })
}

fn resolve_case_artifact_path(case_dir: &Path, repo_root: &Path, raw: &str) -> Option<PathBuf> {
  let trimmed = raw.trim();
  if trimmed.is_empty() || trimmed == "unavailable" || trimmed == "REPLACE_ME" {
    return None;
  }

  let candidate = PathBuf::from(trimmed);
  if candidate.is_absolute() {
    return Some(candidate);
  }

  let repo_relative = repo_root.join(&candidate);
  if repo_relative.exists() {
    return Some(repo_relative);
  }

  Some(case_dir.join(candidate))
}

fn normalize_case_runtime_path(case_dir: &Path, repo_root: &Path, raw: &str) -> String {
  let trimmed = raw.trim();
  if trimmed.is_empty() || trimmed == "unavailable" || trimmed == "REPLACE_ME" {
    return trimmed.to_string();
  }

  let candidate = PathBuf::from(trimmed);
  if candidate.is_absolute() {
    return candidate.display().to_string();
  }

  let repo_relative = repo_root.join(&candidate);
  if repo_relative.exists() {
    return trimmed.to_string();
  }

  let case_relative = case_dir.join(&candidate);
  if let Ok(relative_to_repo) = case_relative.strip_prefix(repo_root) {
    return relative_to_repo.to_string_lossy().replace('\\', "/");
  }

  case_relative.display().to_string()
}

fn discover_imported_source_candidates(
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  search_roots: &[PathBuf],
  repo_root: &Path,
) -> Vec<ImportedSourceCandidate> {
  if search_roots.is_empty() {
    return Vec::new();
  }

  if !analysis.target.archive_members.is_empty() {
    return analysis
      .target
      .archive_members
      .iter()
      .filter_map(|member| {
        discover_imported_source_candidate(
          repo_root,
          search_roots,
          &format!("sources/candidates/{}.c", member.id),
          &[
            format!("sources/candidates/{}.c", member.id),
            format!("{}.c", member.id),
          ],
          "archive-member-candidate",
        )
      })
      .collect();
  }

  discover_imported_source_candidate(
    repo_root,
    search_roots,
    &format!("sources/candidates/{}.c", case.case_id),
    &[
      format!("sources/candidates/{}.c", case.case_id),
      format!("{}.c", case.case_id),
      "candidate.c".to_string(),
      "trial.c".to_string(),
      format!("{}/permuter-best.c", case.workspace.build_dir),
      format!("{}/m2c.c", case.workspace.build_dir),
    ],
    "prompt-source-candidate",
  )
  .into_iter()
  .collect()
}

fn discover_imported_source_candidate(
  repo_root: &Path,
  search_roots: &[PathBuf],
  output_path: &str,
  candidate_paths: &[String],
  kind: &'static str,
) -> Option<ImportedSourceCandidate> {
  let mut attempted = Vec::new();
  for search_root in search_roots {
    for candidate in candidate_paths {
      if let Some(path) = resolve_case_source_candidate_path(search_root, repo_root, candidate) {
        attempted.push(path.display().to_string());
        if path.is_file() {
          return Some(ImportedSourceCandidate {
            output_path: output_path.to_string(),
            source_path: path.clone(),
            kind,
            evidence: vec![
              format!("imported_source={}", path.display()),
              format!("output_path={output_path}"),
            ],
          });
        }
      }
    }
  }
  let _ = attempted;
  None
}

fn resolve_case_source_candidate_path(
  search_root: &Path,
  repo_root: &Path,
  raw: &str,
) -> Option<PathBuf> {
  let trimmed = raw.trim();
  if trimmed.is_empty() {
    return None;
  }

  let candidate = PathBuf::from(trimmed);
  if candidate.is_absolute() {
    return Some(candidate);
  }

  let search_relative = search_root.join(&candidate);
  if search_relative.exists() {
    return Some(search_relative);
  }

  let repo_relative = repo_root.join(&candidate);
  if repo_relative.exists() {
    return Some(repo_relative);
  }

  Some(search_relative)
}

fn apply_imported_source_candidates(
  analysis: &mut AnalysisRecord,
  reconstruction: &mut ReconstructionGraph,
  build_plan: &mut BuildPlan,
  imported_sources: &[ImportedSourceCandidate],
) {
  if imported_sources.is_empty() {
    return;
  }

  let source_lookup = imported_sources
    .iter()
    .map(|candidate| (candidate.output_path.as_str(), candidate))
    .collect::<BTreeMap<_, _>>();

  for candidate in &mut reconstruction.source_candidates {
    if let Some(imported) = source_lookup.get(candidate.path.as_str()) {
      candidate.kind = imported.kind.to_string();
      candidate.status = "candidate".to_string();
      candidate.blocking_reasons = vec![
        "Imported source candidate is available for rebuild and proof runs.".to_string(),
        "Recovered-source status remains unverified until object/binary proof and exact invocation evidence are established.".to_string(),
      ];
      analysis.notes.push(format!(
        "imported_source_candidate={} -> {}",
        imported.source_path.display(),
        candidate.path
      ));
    }
  }

  for unit in &mut reconstruction.project_structure.translation_units {
    if let Some(imported) = source_lookup.get(unit.source_path.as_str()) {
      unit.status = "candidate".to_string();
      unit.kind = imported.kind.to_string();
      unit.blocking_reasons = vec![
        "Imported source candidate is present for this translation unit.".to_string(),
        "Exact compiler invocation and proof status remain unresolved.".to_string(),
      ];
      unit.evidence.extend(imported.evidence.iter().cloned());
    }
  }

  for unit in &mut build_plan.build_units {
    if let Some(imported) = source_lookup.get(unit.source_path.as_str()) {
      unit.status = "candidate".to_string();
      unit.blockers.retain(|blocker| blocker != "No verified recovered source candidate exists.");
      unit.blockers.push(
        "Imported source candidate is available, but verified recovery and exact invocation remain unresolved."
          .to_string(),
      );
      unit.blockers.sort();
      unit.blockers.dedup();
      if let Some(input) = build_plan
        .required_inputs
        .iter_mut()
        .find(|input| input.name == "verified_source")
      {
        input.status = "candidate".to_string();
        input.detail = format!(
          "Imported source candidate available at {}; proof has not established it as verified recovered source yet.",
          imported.source_path.display()
        );
      }
    }
  }
}

fn apply_proof_target_mapping(
  repo_root: &Path,
  case: &CaseManifest,
  analysis: &AnalysisRecord,
  reconstruction: &mut ReconstructionGraph,
  build_plan: &mut BuildPlan,
) {
  if analysis.target.archive_members.is_empty() {
    let status = if case.proof.target_object_path == "unavailable" {
      "unavailable"
    } else if resolve_path(repo_root, &case.proof.target_object_path).is_file() {
      "mapped"
    } else {
      "proof-source-missing"
    };
    if let Some(unit) = build_plan.build_units.first_mut() {
      unit.proof_target = case.proof.target_object_path.clone();
      unit.proof_target_status = status.to_string();
      unit.proof_target_locator = "direct-object".to_string();
      unit.proof_source_path = case.proof.target_object_path.clone();
      unit.proof_target_member_index = None;
      unit.blockers.retain(|blocker| {
        !blocker.starts_with("Configured proof source path does not resolve")
          && !blocker.starts_with("Proof target is unavailable")
      });
      match status {
        "mapped" => {}
        "proof-source-missing" => unit.blockers.push(
          "Configured proof source path does not resolve to a local artifact.".to_string(),
        ),
        _ => unit
          .blockers
          .push("Proof target is unavailable for this build unit.".to_string()),
      }
      unit.blockers.sort();
      unit.blockers.dedup();
    }
  } else {
    let member_lookup = analysis
      .target
      .archive_members
      .iter()
      .map(|member| (member.id.as_str(), member))
      .collect::<BTreeMap<_, _>>();
    let proof_source_path = case.proof.target_object_path.clone();

    let mut resolved = member_lookup
      .values()
      .map(|member| {
        (
          member.id.clone(),
          (
            "unavailable".to_string(),
            "unavailable".to_string(),
            format!("archive-member:{}", member.name),
            None,
            "Per-member proof target is unavailable for this archive member.".to_string(),
          ),
        )
      })
      .collect::<BTreeMap<_, _>>();

    if proof_source_path == "unavailable" {
      // Keep defaults.
    } else {
      let proof_source = resolve_path(repo_root, &proof_source_path);
      if !proof_source.is_file() {
        for member in member_lookup.values() {
          resolved.insert(
            member.id.clone(),
            (
              "unavailable".to_string(),
              "proof-source-missing".to_string(),
              format!("archive-member:{}", member.name),
              None,
              "Configured proof source path does not resolve to a local archive artifact.".to_string(),
            ),
          );
        }
      } else if let Ok(bytes) = fs::read(&proof_source) {
        if let Ok(archive) = ArchiveFile::parse(bytes.as_slice()) {
          #[derive(Debug, Clone)]
          struct ProofArchiveMember {
            index: usize,
            name: String,
            is_thin: bool,
            object_like: bool,
          }

          let mut proof_members = Vec::new();
          for (index, member) in archive.members().enumerate() {
            if let Ok(member) = member {
              let name = String::from_utf8_lossy(member.name()).to_string();
              let member_bytes = member.data(bytes.as_slice()).unwrap_or(&[]);
              proof_members.push(ProofArchiveMember {
                index,
                name,
                is_thin: member.is_thin(),
                object_like: File::parse(member_bytes).is_ok(),
              });
            }
          }

          let mut used = vec![false; proof_members.len()];
          for (member_index, member) in analysis.target.archive_members.iter().enumerate() {
            let name_match = proof_members
              .iter()
              .enumerate()
              .find(|(index, proof_member)| !used[*index] && proof_member.name == member.name)
              .map(|(index, _)| index);
            let fallback_match = if name_match.is_none()
              && proof_members.len() == analysis.target.archive_members.len()
              && proof_members.get(member_index).is_some()
              && !used[member_index]
            {
              Some(member_index)
            } else {
              None
            };
            if let Some(proof_index) = name_match.or(fallback_match) {
              used[proof_index] = true;
              let proof_member = &proof_members[proof_index];
              let (proof_target, status, blocker) = if proof_member.is_thin {
                (
                  "unavailable".to_string(),
                  "thin-member-unavailable".to_string(),
                  "Matched proof archive member is thin and cannot be materialized locally."
                    .to_string(),
                )
              } else if proof_member.object_like {
                (
                  format!("proof/members/{}.o", member.id),
                  "mapped".to_string(),
                  String::new(),
                )
              } else {
                (
                  "unavailable".to_string(),
                  "proof-source-unparsed".to_string(),
                  "Matched proof archive member did not parse as an object file.".to_string(),
                )
              };
              resolved.insert(
                member.id.clone(),
                (
                  proof_target,
                  status,
                  format!("archive-member:{}@{}", proof_member.name, proof_member.index),
                  Some(proof_member.index),
                  blocker,
                ),
              );
            } else {
              resolved.insert(
                member.id.clone(),
                (
                  "unavailable".to_string(),
                  "missing-member-match".to_string(),
                  format!("archive-member:{}", member.name),
                  None,
                  "No matching proof archive member was found for this build unit.".to_string(),
                ),
              );
            }
          }
        } else {
          for member in member_lookup.values() {
            resolved.insert(
              member.id.clone(),
              (
                "unavailable".to_string(),
                "proof-source-unparsed".to_string(),
                format!("archive-member:{}", member.name),
                None,
                "Configured proof source could not be parsed as an archive.".to_string(),
              ),
            );
          }
        }
      } else {
        for member in member_lookup.values() {
          resolved.insert(
            member.id.clone(),
            (
              "unavailable".to_string(),
              "proof-source-missing".to_string(),
              format!("archive-member:{}", member.name),
              None,
              "Configured proof source path could not be read.".to_string(),
            ),
          );
        }
      }
    }

    for unit in &mut build_plan.build_units {
      if let Some((proof_target, status, locator, member_index, blocker)) = resolved.get(&unit.id) {
        unit.proof_target = proof_target.clone();
        unit.proof_target_status = status.clone();
        unit.proof_target_locator = locator.clone();
        unit.proof_source_path = proof_source_path.clone();
        unit.proof_target_member_index = *member_index;
        unit.blockers.retain(|item| {
          item != "Per-member proof targets and librarian invocation are unresolved."
            && !item.starts_with("Configured proof source")
            && !item.starts_with("Matched proof archive member")
            && !item.starts_with("No matching proof archive member")
            && !item.starts_with("Per-member proof target is unavailable")
        });
        if status != "mapped" && !blocker.is_empty() {
          unit.blockers.push(blocker.clone());
        }
        unit.blockers.sort();
        unit.blockers.dedup();
      }
    }
  }

  let unit_lookup = build_plan
    .build_units
    .iter()
    .map(|unit| (unit.id.as_str(), unit))
    .collect::<BTreeMap<_, _>>();
  for unit in &mut reconstruction.project_structure.translation_units {
    if let Some(mapped) = unit_lookup.get(unit.id.as_str()) {
      unit.proof_target = mapped.proof_target.clone();
    }
  }

  let mapped_count = build_plan
    .build_units
    .iter()
    .filter(|unit| unit.proof_target_status == "mapped")
    .count();
  if let Some(requirement) = build_plan
    .required_inputs
    .iter_mut()
    .find(|input| input.name == "proof_target")
  {
    let total = build_plan.build_units.len();
    requirement.status = if mapped_count == 0 {
      "missing".to_string()
    } else if mapped_count == total {
      "available".to_string()
    } else {
      "partial".to_string()
    };
    requirement.detail = if total > 1 {
      format!(
        "{mapped_count}/{total} build units have mapped proof targets from {}.",
        case.proof.target_object_path
      )
    } else {
      match build_plan.build_units.first().map(|unit| unit.proof_target_status.as_str()) {
        Some("mapped") => case.proof.target_object_path.clone(),
        Some("proof-source-missing") => {
          "Configured proof source path does not resolve to a local artifact.".to_string()
        }
        _ => "Proof target is unavailable for the current build unit.".to_string(),
      }
    };
  }
}

fn materialize_source_candidates(
  project: &Path,
  case_id: &str,
  reconstruction: &ReconstructionGraph,
  imported_sources: &[ImportedSourceCandidate],
) -> Result<()> {
  let source_lookup = imported_sources
    .iter()
    .map(|candidate| (candidate.output_path.as_str(), candidate))
    .collect::<BTreeMap<_, _>>();

  for candidate in &reconstruction.source_candidates {
    let candidate_abs = project.join(&candidate.path);
    if let Some(parent) = candidate_abs.parent() {
      fs::create_dir_all(parent)?;
    }
    if let Some(imported) = source_lookup.get(candidate.path.as_str()) {
      let contents = fs::read_to_string(&imported.source_path).with_context(|| {
        format!(
          "failed to read imported source candidate {}",
          imported.source_path.display()
        )
      })?;
      fs::write(&candidate_abs, contents)
        .with_context(|| format!("failed to write {}", candidate_abs.display()))?;
      continue;
    }

    let stub_id = candidate
      .path
      .rsplit('/')
      .next()
      .and_then(|name| name.strip_suffix(".c"))
      .unwrap_or(case_id);
    fs::write(&candidate_abs, blocked_source_candidate(stub_id))
      .with_context(|| format!("failed to write {}", candidate_abs.display()))?;
  }
  Ok(())
}

fn materialize_proof_targets(
  project: &Path,
  build_plan: &BuildPlan,
  analysis: &AnalysisRecord,
  repo_root: &Path,
) -> Result<()> {
  let mapped_archive_units = build_plan
    .build_units
    .iter()
    .filter_map(|unit| {
      if unit.proof_target_status == "mapped" {
        unit
          .proof_target_member_index
          .map(|index| (index, unit.proof_target.as_str()))
      } else {
        None
      }
    })
    .collect::<BTreeMap<_, _>>();
  if mapped_archive_units.is_empty() || analysis.target.archive_members.is_empty() {
    return Ok(());
  }

  let Some(proof_source_path) = build_plan
    .build_units
    .iter()
    .find(|unit| unit.proof_target_status == "mapped")
    .map(|unit| unit.proof_source_path.as_str())
  else {
    return Ok(());
  };
  let proof_source = resolve_path(repo_root, proof_source_path);
  let bytes = fs::read(&proof_source)
    .with_context(|| format!("failed to read proof archive {}", proof_source.display()))?;
  let archive = ArchiveFile::parse(bytes.as_slice())
    .with_context(|| format!("failed to parse proof archive {}", proof_source.display()))?;
  let mut written = BTreeSet::new();

  for (index, member) in archive.members().enumerate() {
    let Some(target_rel) = mapped_archive_units.get(&index) else {
      continue;
    };
    let member = member.with_context(|| {
      format!(
        "failed to read proof archive member #{index} from {}",
        proof_source.display()
      )
    })?;
    let member_bytes = member
      .data(bytes.as_slice())
      .with_context(|| {
        format!(
          "proof archive member #{index} from {} has no materializable data",
          proof_source.display()
        )
      })?;
    let target_path = project.join(target_rel);
    if let Some(parent) = target_path.parent() {
      fs::create_dir_all(parent)?;
    }
    fs::write(&target_path, member_bytes)
      .with_context(|| format!("failed to write {}", target_path.display()))?;
    written.insert(index);
  }

  if written.len() != mapped_archive_units.len() {
    let missing = mapped_archive_units
      .keys()
      .filter(|index| !written.contains(index))
      .map(|index| index.to_string())
      .collect::<Vec<_>>()
      .join(", ");
    return Err(anyhow!(
      "failed to materialize mapped proof archive members: {missing}"
    ));
  }

  Ok(())
}

#[derive(Debug, serde::Deserialize, Default)]
struct MizuchiConfigFile {
  global: Option<MizuchiGlobalConfig>,
}

#[derive(Debug, serde::Deserialize, Default)]
struct MizuchiGlobalConfig {
  #[serde(rename = "compilerScript")]
  compiler_script: Option<String>,
}

fn resolve_runtime_compiler_config(
  search_roots: &[PathBuf],
  repo_root: &Path,
) -> RuntimeCompilerConfig {
  let default_template =
    "bash ./scripts/compile-placeholder.sh \"{{cFilePath}}\" \"{{objFilePath}}\"".to_string();

  if let Ok(override_path) = std::env::var("DECOMP_MIZUCHI_CONFIG") {
    let override_path = PathBuf::from(override_path);
    if override_path.is_file() {
      if let Ok(config) = parse_runtime_compiler_config(&override_path) {
        return config;
      }
    }
  }

  for root in candidate_config_roots(search_roots, repo_root) {
    let config_path = root.join("mizuchi.yaml");
    if config_path.is_file() {
      if let Ok(config) = parse_runtime_compiler_config(&config_path) {
        return config;
      } else {
        return RuntimeCompilerConfig {
          template: default_template.clone(),
          runtime_root: repo_root.to_path_buf(),
          config_path: Some(config_path.clone()),
          rebuild_supported: false,
          uses_placeholder: true,
          evidence: vec![
            format!("compiler_config_path={}", config_path.display()),
            "compiler_config_parse=failed".to_string(),
          ],
          uncertainties: vec![UncertaintyItem::blocking(
            "mizuchi-config-parse-failed",
            FailureClass::InfraError,
            "mizuchi.yaml exists but could not be parsed; falling back to the placeholder compile driver.",
            vec![config_path.display().to_string()],
          )],
        };
      }
    }
    let example_path = root.join("mizuchi.example.yaml");
    if example_path.is_file() {
      if let Ok(config) = parse_runtime_compiler_config(&example_path) {
        return config;
      }
    }
  }

  RuntimeCompilerConfig {
    template: default_template,
    runtime_root: repo_root.to_path_buf(),
    config_path: None,
    rebuild_supported: false,
    uses_placeholder: true,
    evidence: vec!["compiler_config_source=default-placeholder".to_string()],
    uncertainties: Vec::new(),
  }
}

fn candidate_config_roots(search_roots: &[PathBuf], repo_root: &Path) -> Vec<PathBuf> {
  let mut seen = BTreeSet::new();
  let mut roots = Vec::new();

  for root in search_roots {
    for ancestor in root.ancestors() {
      if seen.insert(ancestor.to_path_buf()) {
        roots.push(ancestor.to_path_buf());
      }
    }
  }

  if seen.insert(repo_root.to_path_buf()) {
    roots.push(repo_root.to_path_buf());
  }

  roots
}

fn parse_runtime_compiler_config(path: &Path) -> Result<RuntimeCompilerConfig> {
  let raw = fs::read_to_string(path)
    .with_context(|| format!("failed to read mizuchi config {}", path.display()))?;
  let parsed: MizuchiConfigFile = serde_yaml::from_str(&raw)
    .with_context(|| format!("failed to parse mizuchi config {}", path.display()))?;
  let template = parsed
    .global
    .and_then(|global| global.compiler_script)
    .map(|script| script.trim().to_string())
    .filter(|script| !script.is_empty())
    .unwrap_or_else(|| {
      "bash ./scripts/compile-placeholder.sh \"{{cFilePath}}\" \"{{objFilePath}}\"".to_string()
    });
  let runtime_root = path
    .parent()
    .map(Path::to_path_buf)
    .unwrap_or_else(|| PathBuf::from("."));
  let uses_placeholder = template.contains("compile-placeholder.sh");
  Ok(RuntimeCompilerConfig {
    evidence: vec![
      format!("compiler_config_path={}", path.display()),
      format!("compiler_script_placeholder={uses_placeholder}"),
    ],
    uncertainties: Vec::new(),
    rebuild_supported: !uses_placeholder,
    uses_placeholder,
    template,
    runtime_root,
    config_path: Some(path.to_path_buf()),
  })
}

fn apply_runtime_compiler_config(
  build_plan: &mut BuildPlan,
  config: &RuntimeCompilerConfig,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  build_plan.compiler_script = config.template.clone();
  build_plan.compiler_script_root = config.runtime_root.display().to_string();
  build_plan.compiler_config_path = config
    .config_path
    .as_ref()
    .map(|path| path.display().to_string());
  build_plan.rebuild_supported = config.rebuild_supported;
  build_plan
    .toolchain
    .evidence
    .extend(config.evidence.iter().cloned());
  for unit in &mut build_plan.build_units {
    unit.blockers.retain(|blocker| {
      !(config.rebuild_supported
        && blocker == "A target-specific compiler configuration has not been fully recovered.")
    });
  }
  if config.rebuild_supported {
    build_plan.blockers.retain(|blocker| {
      blocker != "A target-specific compiler configuration has not been fully recovered."
    });
  } else if config.uses_placeholder {
    build_plan.blockers.push(
      "The resolved compilerScript still points at the placeholder driver and cannot produce real rebuild proof."
        .to_string(),
    );
  }
  uncertainty.items.extend(config.uncertainties.iter().cloned());
}

fn apply_analysis_provider_availability(
  analysis: &AnalysisRecord,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  let missing_external_providers = analysis
    .tool_availability
    .analysis_providers
    .iter()
    .filter(|provider| !provider.available && provider.kind != "in-process")
    .map(|provider| format!("{}:{}({})", provider.role, provider.id, provider.kind))
    .collect::<Vec<_>>();

  if missing_external_providers.is_empty() {
    verification.set_check(VerificationCheck::passed(
      "analysis_provider_availability",
      true,
      "All configured external analysis providers are available.",
    ));
  } else {
    verification.add_failure(FailureClass::ToolMissing);
    verification.set_check(VerificationCheck::failed(
      "analysis_provider_availability",
      true,
      format!(
        "Configured external analysis providers are unavailable: {}.",
        missing_external_providers.join(", ")
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "analysis-provider-missing",
      FailureClass::ToolMissing,
      "Required external analysis infrastructure is unavailable for this adapter.",
      missing_external_providers,
    ));
  }
}

fn apply_type_relation_graph(
  graph: &crate::model::TypeRelationGraph,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  verification.set_check(VerificationCheck::passed(
    "type_relation_inventory",
    true,
    format!(
      "Recorded {} symbol node(s), {} type candidate(s), and {} relationship edge(s).",
      graph.symbol_count, graph.type_candidate_count, graph.relationship_count
    ),
  ));
  if graph.unresolved_type_count > 0 {
    uncertainty.items.push(UncertaintyItem::blocking(
      "type-relations-unresolved",
      FailureClass::SemanticUnknown,
      "Type relationships are evidence-only; signatures, layouts, RTTI hierarchy, and templates remain unresolved.",
      graph.uncertainty.clone(),
    ));
  }
}

fn apply_cfg_evidence_graph(
  graph: &crate::model::CfgEvidenceGraph,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  verification.set_check(VerificationCheck::passed(
    "cfg_inventory",
    true,
    format!(
      "Recorded {} function boundary CFG row(s); {} remain unresolved and {} CFG edge(s) are proven.",
      graph.function_count, graph.unresolved_function_count, graph.edge_count
    ),
  ));
  verification.set_check(VerificationCheck::skipped(
    "cfg_comparison",
    true,
    format!(
      "CFG comparison blocked: target_cfg_available={}, candidate_cfg_available={}.",
      graph.comparison_readiness.target_cfg_available,
      graph.comparison_readiness.candidate_cfg_available
    ),
  ));
  if graph.unresolved_function_count > 0 || !graph.comparison_readiness.candidate_cfg_available {
    uncertainty.items.push(UncertaintyItem::blocking(
      "cfg-comparison-unavailable",
      FailureClass::SemanticUnknown,
      "CFG evidence is boundary-only; basic blocks, branch edges, and candidate CFG comparison are unresolved.",
      graph
        .comparison_readiness
        .blockers
        .iter()
        .cloned()
        .chain(graph.uncertainty.iter().cloned())
        .collect(),
    ));
  }
}

fn apply_dependency_graph(
  graph: &crate::model::DependencyGraph,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  verification.set_check(VerificationCheck::passed(
    "dependency_inventory",
    true,
    format!(
      "Recorded {} import(s), {} export(s), {} relocation edge(s), and {} runtime artifact(s).",
      graph.import_count,
      graph.export_count,
      graph.relocation_edge_count,
      graph.runtime_artifact_count
    ),
  ));
  if graph.unresolved_dependency_count > 0 {
    uncertainty.items.push(UncertaintyItem::blocking(
      "dependencies-unresolved",
      FailureClass::CompilerUnknown,
      "Dependency graph is evidence-only; exact library paths, versions, sysroots, import libraries, and linker scripts remain unresolved.",
      graph.uncertainty.clone(),
    ));
  }
}

fn audit_source_artifacts(
  project: &Path,
  case_id: &str,
  reconstruction: &ReconstructionGraph,
) -> Result<SourceAuditRecord> {
  let artifacts = reconstruction
    .source_candidates
    .iter()
    .map(|candidate| {
      let artifact_path = project.join(&candidate.path);
      let contents = fs::read_to_string(&artifact_path)
        .with_context(|| format!("failed to read {}", artifact_path.display()))?;
      Ok(audit_source_artifact(
        &candidate.path,
        &candidate.language,
        &candidate.kind,
        &contents,
      ))
    })
    .collect::<Result<Vec<_>>>()?;

  let artifact_count = artifacts.len();
  let blocked_stub_count = artifacts
    .iter()
    .filter(|artifact| artifact.verdict == "marked-blocking-stub")
    .count();
  let suspicious_count = artifacts
    .iter()
    .filter(|artifact| artifact.verdict == "policy-violation")
    .count();
  let status = if suspicious_count == 0 {
    "passed"
  } else {
    "failed"
  };

  Ok(SourceAuditRecord {
    schema_version: 1,
    generated_at: crate::model::now_rfc3339(),
    case_id: case_id.to_string(),
    status: status.to_string(),
    artifact_count,
    blocked_stub_count,
    suspicious_count,
    artifacts,
  })
}

fn audit_source_artifact(path: &str, language: &str, kind: &str, contents: &str) -> SourceArtifactAudit {
  let marked_blocking = contents.contains("Evidence-only source candidate generated by decomp")
    && contents.contains("Status: incomplete")
    && contents.contains("Recovery incomplete");
  let compile_blocking = contents.contains("#error") || contents.contains("compile_error!");
  let contains_hardcoded_address = contains_hardcoded_address(contents);
  let contains_placeholder_marker = contains_placeholder_marker(contents);
  let contains_fabrication_marker = contains_fabrication_marker(contents);
  let contains_unmarked_placeholder = contains_placeholder_marker && !marked_blocking;
  let policy_violation =
    contains_hardcoded_address || contains_unmarked_placeholder || contains_fabrication_marker;
  let verdict = if policy_violation {
    "policy-violation"
  } else if marked_blocking && compile_blocking {
    "marked-blocking-stub"
  } else {
    "unverified-source"
  };
  let mut evidence = vec![
    format!("marked_blocking={marked_blocking}"),
    format!("compile_blocking={compile_blocking}"),
    format!("contains_hardcoded_address={contains_hardcoded_address}"),
    format!("contains_unmarked_placeholder={contains_unmarked_placeholder}"),
    format!("contains_fabrication_marker={contains_fabrication_marker}"),
  ];
  if verdict == "unverified-source" {
    evidence.push(
      "source artifact requires rebuild and binary/object proof before it can be treated as recovered"
        .to_string(),
    );
  }

  SourceArtifactAudit {
    path: path.to_string(),
    language: language.to_string(),
    kind: kind.to_string(),
    verdict: verdict.to_string(),
    marked_blocking,
    compile_blocking,
    contains_hardcoded_address,
    contains_unmarked_placeholder,
    contains_fabrication_marker,
    evidence,
  }
}

fn contains_hardcoded_address(contents: &str) -> bool {
  contents
    .split(|ch: char| !ch.is_ascii_hexdigit() && ch != 'x' && ch != 'X')
    .any(|token| {
      let Some(hex) = token.strip_prefix("0x").or_else(|| token.strip_prefix("0X")) else {
        return false;
      };
      hex.len() >= 6 && hex.chars().all(|ch| ch.is_ascii_hexdigit())
    })
}

fn contains_placeholder_marker(contents: &str) -> bool {
  let lower = contents.to_ascii_lowercase();
  ["todo", "placeholder", "stub", "dummy", "unimplemented"]
    .iter()
    .any(|marker| lower.contains(marker))
}

fn contains_fabrication_marker(contents: &str) -> bool {
  let lower = contents.to_ascii_lowercase();
  [
    "best guess",
    "good enough",
    "fake implementation",
    "fabricated",
    "guessed logic",
  ]
  .iter()
  .any(|marker| lower.contains(marker))
}

fn apply_source_audit(
  audit: &SourceAuditRecord,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  if audit.status == "passed" {
    verification.set_check(VerificationCheck::passed(
      "source_artifact_audit",
      true,
      format!(
        "Audited {} source artifact(s): {} marked blocking stub(s), {} policy violation(s).",
        audit.artifact_count, audit.blocked_stub_count, audit.suspicious_count
      ),
    ));
  } else {
    verification.add_failure(FailureClass::SemanticUnknown);
    verification.set_check(VerificationCheck::failed(
      "source_artifact_audit",
      true,
      format!(
        "Source audit found {} policy violation(s) across {} artifact(s).",
        audit.suspicious_count, audit.artifact_count
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "source-policy-violation",
      FailureClass::SemanticUnknown,
      "Generated source artifacts contain unmarked placeholders, hardcoded addresses, or fabrication markers.",
      audit
        .artifacts
        .iter()
        .filter(|artifact| artifact.verdict == "policy-violation")
        .map(|artifact| artifact.path.clone())
        .collect(),
    ));
  }
}

fn apply_compiler_invocation_ledger(
  ledger: &crate::model::CompilerInvocationLedger,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) {
  if ledger.recovered_invocation_count > 0 {
    verification.set_check(VerificationCheck::passed(
      "compiler_invocation_contract",
      true,
      format!(
        "{} recovered invocation(s) across {} candidate(s).",
        ledger.recovered_invocation_count, ledger.candidate_count
      ),
    ));
  } else {
    verification.set_check(VerificationCheck::failed(
      "compiler_invocation_contract",
      true,
      format!(
        "No exact compiler invocation recovered across {} candidate profile/build-unit row(s).",
        ledger.candidate_count
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "compiler-invocation-unresolved",
      FailureClass::CompilerUnknown,
      "Exact compiler path, version, arguments, environment, and runtime inputs are unresolved.",
      ledger.missing_evidence.clone(),
    ));
  }
}

fn recover_replayed_compiler_invocations(
  project: &Path,
  build_plan: &BuildPlan,
  function_name: &str,
  verification: &VerificationRecord,
  ledger: &mut crate::model::CompilerInvocationLedger,
) {
  if !build_plan.rebuild_supported {
    return;
  }

  let proven_units = verification
    .build_unit_proof_results
    .iter()
    .filter(|result| {
      result.rebuild_status == "passed"
        && result.object_match_status == "passed"
        && result.binary_diff_status == "passed"
    })
    .map(|result| result.build_unit_id.as_str())
    .collect::<BTreeSet<_>>();
  if proven_units.is_empty() {
    return;
  }

  let compiler_root = resolve_path(project, &build_plan.compiler_script_root);
  let mut recovered_units = BTreeSet::new();
  for unit in &build_plan.build_units {
    if !proven_units.contains(unit.id.as_str()) || recovered_units.contains(unit.id.as_str()) {
      continue;
    }
    let unit_function_name = if build_plan.build_units.len() == 1 {
      function_name
    } else {
      unit.id.as_str()
    };
    let source = project.join(&unit.source_path);
    let object = project.join(&unit.object_path);
    let expanded_script =
      expand_compiler_script_template(&build_plan.compiler_script, &source, &object, unit_function_name);
    let source_sensitive = compiler_script_is_source_sensitive(
      project,
      build_plan,
      unit,
      unit_function_name,
      &source,
      &object,
    )
    .unwrap_or(false);
    if !source_sensitive {
      if let Some(invocation) = ledger
        .invocations
        .iter_mut()
        .find(|candidate| candidate.build_unit_id == unit.id && !candidate.exact_command_recovered)
      {
        invocation.evidence.extend([
          "replay_scope=current-candidate-rebuild".to_string(),
          "source_sensitivity_probe=failed".to_string(),
          "exact_invocation_not_recovered=compiler script did not prove source participation".to_string(),
        ]);
      }
      continue;
    }
    if let Some(invocation) = ledger
      .invocations
      .iter_mut()
      .find(|candidate| candidate.build_unit_id == unit.id && !candidate.exact_command_recovered)
    {
      invocation.status = "recovered".to_string();
      invocation.exact_command_recovered = true;
      invocation.argument_vector = vec![
        "bash".to_string(),
        "-lc".to_string(),
        expanded_script.clone(),
      ];
      for requirement in &mut invocation.environment {
        if requirement.name == "working_directory" {
          requirement.status = "recovered".to_string();
          requirement.detail = compiler_root.display().to_string();
        }
      }
      invocation.blockers.clear();
      invocation.evidence.extend([
        "recovery_mode=configured-compiler-script-replay".to_string(),
        "replay_scope=current-candidate-rebuild".to_string(),
        "source_sensitivity_probe=passed".to_string(),
        "original_toolchain_identity=not-claimed".to_string(),
        format!("compiler_script_root={}", compiler_root.display()),
        format!("compiler_script_template={}", build_plan.compiler_script),
        format!("expanded_compiler_script={expanded_script}"),
        format!("source_path={}", source.display()),
        format!("object_path={}", object.display()),
        format!("proof_target={}", unit.proof_target),
        "rebuild_status=passed".to_string(),
        "object_match_status=passed".to_string(),
        "binary_diff_status=passed".to_string(),
      ]);
      recovered_units.insert(unit.id.as_str());
    }
  }

  ledger.recovered_invocation_count = ledger
    .invocations
    .iter()
    .filter(|invocation| invocation.exact_command_recovered)
    .count();
  if ledger.recovered_invocation_count > 0 {
    ledger.status = if ledger.recovered_invocation_count == build_plan.build_units.len() {
      "recovered".to_string()
    } else {
      "partial".to_string()
    };
    ledger.missing_evidence = vec![
      "Original compiler executable/version identity remains unclaimed unless separately proven."
        .to_string(),
      "Recovered invocation rows are exact replay commands for the current candidate rebuild."
        .to_string(),
    ];
  }
}

fn compiler_script_is_source_sensitive(
  project: &Path,
  build_plan: &BuildPlan,
  unit: &BuildUnitPlan,
  function_name: &str,
  source: &Path,
  object: &Path,
) -> Result<bool> {
  let original_object = fs::read(object)
    .with_context(|| format!("failed to read rebuilt object {}", object.display()))?;
  let source_bytes =
    fs::read(source).with_context(|| format!("failed to read source {}", source.display()))?;
  let probe_dir = project.join("build/probes");
  fs::create_dir_all(&probe_dir)?;
  let probe_source = probe_dir.join(format!("{}-source-sensitivity.c", unit.id));
  let probe_object = probe_dir.join(format!("{}-source-sensitivity.o", unit.id));
  let mut mutated_source = source_bytes;
  mutated_source.extend_from_slice(
    b"\n#error mizuchi source sensitivity probe: this source must affect rebuild output\n",
  );
  fs::write(&probe_source, mutated_source)?;
  let _ = fs::remove_file(&probe_object);

  let compiler_root = resolve_path(project, &build_plan.compiler_script_root);
  let expanded_script =
    expand_compiler_script_template(&build_plan.compiler_script, &probe_source, &probe_object, function_name);
  let output = Command::new("bash")
    .arg("-lc")
    .arg(&expanded_script)
    .current_dir(&compiler_root)
    .output()
    .with_context(|| {
      format!(
        "failed to execute source sensitivity probe from {}",
        compiler_root.display()
      )
    })?;

  if !output.status.success() {
    return Ok(true);
  }
  let Ok(probe_object_bytes) = fs::read(&probe_object) else {
    return Ok(true);
  };
  Ok(probe_object_bytes != original_object)
}

pub fn load_project(project: &Path) -> Result<ProjectSnapshot> {
  let case = fs::read_to_string(project.join("case.yaml"))
    .with_context(|| format!("failed to read {}/case.yaml", project.display()))?;
  let analysis = fs::read_to_string(project.join("analysis.json"))
    .with_context(|| format!("failed to read {}/analysis.json", project.display()))?;
  let reconstruction = fs::read_to_string(project.join("reconstruction.json"))
    .with_context(|| format!("failed to read {}/reconstruction.json", project.display()))?;
  let cfg_evidence = fs::read_to_string(project.join("cfg-evidence.json"))
    .with_context(|| format!("failed to read {}/cfg-evidence.json", project.display()))?;
  let type_relations = fs::read_to_string(project.join("type-relations.json"))
    .with_context(|| format!("failed to read {}/type-relations.json", project.display()))?;
  let dependency_graph = fs::read_to_string(project.join("dependency-graph.json"))
    .with_context(|| format!("failed to read {}/dependency-graph.json", project.display()))?;
  let build_plan = fs::read_to_string(project.join("build-plan.json"))
    .with_context(|| format!("failed to read {}/build-plan.json", project.display()))?;
  let compiler_invocation = fs::read_to_string(project.join("compiler-invocation.json"))
    .with_context(|| format!("failed to read {}/compiler-invocation.json", project.display()))?;
  let source_audit = fs::read_to_string(project.join("source-audit.json"))
    .with_context(|| format!("failed to read {}/source-audit.json", project.display()))?;
  let verification = fs::read_to_string(project.join("verification.json"))
    .with_context(|| format!("failed to read {}/verification.json", project.display()))?;
  let uncertainty = fs::read_to_string(project.join("uncertainty.json"))
    .with_context(|| format!("failed to read {}/uncertainty.json", project.display()))?;

  Ok(ProjectSnapshot {
    actions: ActionSummary {
      rebuild: false,
      verify: false,
      match_requested: false,
    },
    case: serde_yaml::from_str(&case)?,
    analysis: serde_json::from_str(&analysis)?,
    reconstruction: serde_json::from_str(&reconstruction)?,
    cfg_evidence: serde_json::from_str(&cfg_evidence)?,
    type_relations: serde_json::from_str(&type_relations)?,
    dependency_graph: serde_json::from_str(&dependency_graph)?,
    build_plan: serde_json::from_str(&build_plan)?,
    compiler_invocation: serde_json::from_str(&compiler_invocation)?,
    source_audit: serde_json::from_str(&source_audit)?,
    verification: serde_json::from_str(&verification)?,
    uncertainty: serde_json::from_str(&uncertainty)?,
  })
}

pub fn render_report(project: &Path, format: ReportFormat) -> Result<String> {
  let snapshot = load_project(project)?;
  report::render(&snapshot, format)
}

pub fn compare_artifacts(target: &Path, candidate: &Path) -> Result<ArtifactComparison> {
  let target_bytes =
    fs::read(target).with_context(|| format!("failed to read {}", target.display()))?;
  let candidate_bytes =
    fs::read(candidate).with_context(|| format!("failed to read {}", candidate.display()))?;
  let target_fingerprint = artifact_fingerprint(target, &target_bytes)?;
  let candidate_fingerprint = artifact_fingerprint(candidate, &candidate_bytes)?;

  Ok(ArtifactComparison {
    byte_equal: target_bytes == candidate_bytes,
    first_mismatch_offset: first_mismatch_offset(&target_bytes, &candidate_bytes),
    section_inventory_equal: target_fingerprint.sections == candidate_fingerprint.sections,
    symbol_inventory_equal: target_fingerprint.symbols == candidate_fingerprint.symbols,
    relocation_inventory_equal: target_fingerprint.relocations == candidate_fingerprint.relocations,
    target: target_fingerprint,
    candidate: candidate_fingerprint,
  })
}

fn write_project(project: &Path, snapshot: &ProjectSnapshot, repo_root: &Path) -> Result<()> {
  fs::create_dir_all(project.join("build-system"))?;
  materialize_proof_targets(project, &snapshot.build_plan, &snapshot.analysis, repo_root)?;
  write_yaml(&project.join("case.yaml"), &snapshot.case)?;
  write_json(&project.join("analysis.json"), &snapshot.analysis)?;
  write_json(
    &project.join("reconstruction.json"),
    &snapshot.reconstruction,
  )?;
  write_json(&project.join("cfg-evidence.json"), &snapshot.cfg_evidence)?;
  write_json(&project.join("type-relations.json"), &snapshot.type_relations)?;
  write_json(&project.join("dependency-graph.json"), &snapshot.dependency_graph)?;
  write_json(&project.join("build-plan.json"), &snapshot.build_plan)?;
  write_json(
    &project.join("proof-targets.json"),
    &crate::model::derive_proof_target_ledger(snapshot),
  )?;
  write_json(
    &project.join("compiler-invocation.json"),
    &snapshot.compiler_invocation,
  )?;
  write_json(&project.join("source-audit.json"), &snapshot.source_audit)?;
  write_json(
    &project.join("build-unit-verification.json"),
    &crate::model::derive_build_unit_verification_ledger(snapshot),
  )?;
  write_json(
    &project.join("source-verification.json"),
    &crate::model::derive_source_verification_ledger(snapshot),
  )?;
  write_json(
    &project.join("build-graph.json"),
    &build_graph_config(project, snapshot),
  )?;
  write_json(
    &project.join("build-manifest.json"),
    &build_manifest_config(project, snapshot),
  )?;
  write_json(
    &project.join("toolchain-manifest.json"),
    &toolchain_manifest_config(project, snapshot),
  )?;
  write_json(
    &project.join("attempt-matrix.json"),
    &attempt_matrix_config(project, snapshot),
  )?;
  write_json(
    &project.join("upstream-evidence.json"),
    &upstream_evidence_config(project, snapshot),
  )?;
  write_json(
    &project.join("compiler-compatibility.json"),
    &crate::model::derive_compiler_compatibility_ledger(snapshot),
  )?;
  write_json(&project.join("verification.json"), &snapshot.verification)?;
  write_json(
    &project.join("verification-matrix.json"),
    &crate::model::derive_verification_matrix(snapshot),
  )?;
  write_json(
    &project.join("roundtrip.json"),
    &crate::model::derive_roundtrip_proof(snapshot),
  )?;
  write_json(
    &project.join("byte-equivalence.json"),
    &crate::model::derive_byte_equivalence_ledger(snapshot),
  )?;
  write_json(
    &project.join("drift-analysis.json"),
    &crate::model::derive_drift_analysis(snapshot),
  )?;
  write_json(&project.join("uncertainty.json"), &snapshot.uncertainty)?;
  write_json(
    &project.join("objdiff.json"),
    &objdiff_config(project, snapshot),
  )?;
  let objdiff_driver = project.join("build-system/compile-unit.sh");
  fs::write(&objdiff_driver, objdiff_build_driver(snapshot, repo_root))?;
  #[cfg(unix)]
  {
    let mut permissions = fs::metadata(&objdiff_driver)?.permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(&objdiff_driver, permissions)?;
  }
  for artifact in &snapshot.build_plan.build_system.generated_artifacts {
    let artifact_path = project.join(&artifact.path);
    if let Some(parent) = artifact_path.parent() {
      fs::create_dir_all(parent)?;
    }
    fs::write(
      &artifact_path,
      generated_build_backend(snapshot, &artifact.backend, &artifact.path),
    )?;
  }
  fs::write(
    project.join("report.md"),
    report::render(snapshot, ReportFormat::Markdown)?,
  )?;
  Ok(())
}

fn build_graph_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  let translation_units = snapshot
    .reconstruction
    .project_structure
    .translation_units
    .iter()
    .map(|unit| {
      json!({
        "id": unit.id,
        "kind": "translation-unit",
        "source_path": unit.source_path,
        "object_path": unit.object_path,
        "status": unit.status,
        "compiler_profile_candidates": unit.compiler_profile_candidates,
        "depends_on": snapshot
          .build_plan
          .toolchain
          .stages
          .iter()
          .map(|stage| stage.id.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let toolchain_stages = snapshot
    .build_plan
    .toolchain
    .stages
    .iter()
    .map(|stage| {
      json!({
        "id": stage.id,
        "kind": "toolchain-stage",
        "name": stage.name,
        "status": stage.status,
        "required_components": stage.required_components,
        "candidate_profiles": stage.candidate_profiles,
      })
    })
    .collect::<Vec<_>>();
  let link_units = snapshot
    .reconstruction
    .project_structure
    .link_units
    .iter()
    .map(|unit| {
      json!({
        "id": unit.id,
        "kind": "link-unit",
        "artifact_path": unit.artifact_path,
        "status": unit.status,
        "linker_profile_candidates": unit.linker_profile_candidates,
        "depends_on": snapshot
          .reconstruction
          .project_structure
          .translation_units
          .iter()
          .map(|tu| tu.id.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let function_boundaries = snapshot
    .reconstruction
    .functions
    .iter()
    .map(|function| {
      json!({
        "id": function.name,
        "kind": "function-boundary",
        "status": function.cfg_status,
        "source": function.source,
        "confidence": function.confidence,
        "depends_on": snapshot
          .reconstruction
          .project_structure
          .translation_units
          .iter()
          .map(|tu| tu.id.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let cfg_functions = snapshot
    .cfg_evidence
    .functions
    .iter()
    .map(|function| {
      json!({
        "id": function.id,
        "kind": "cfg-function",
        "name": function.name,
        "status": function.status,
        "confidence": function.confidence,
        "basic_block_count": function.basic_block_count,
        "edge_count": function.edge_count,
        "depends_on": snapshot
          .reconstruction
          .functions
          .iter()
          .filter(|boundary| boundary.name == function.name)
          .map(|boundary| boundary.name.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let type_candidates = snapshot
    .type_relations
    .type_candidates
    .iter()
    .map(|candidate| {
      json!({
        "id": candidate.id,
        "kind": "type-candidate",
        "type_kind": candidate.kind,
        "status": candidate.status,
        "confidence": candidate.confidence,
        "source_symbols": candidate.source_symbols,
        "depends_on": candidate
          .source_symbols
          .iter()
          .map(|symbol| format!("symbol:{}", crate::model::sanitize_identifier(symbol)))
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let dependency_imports = snapshot
    .dependency_graph
    .imports
    .iter()
    .map(|import| {
      json!({
        "id": import.id,
        "kind": "dependency-import",
        "library": import.library,
        "symbol": import.symbol,
        "status": import.status,
        "confidence": import.confidence,
        "depends_on": snapshot
          .build_plan
          .toolchain
          .stages
          .iter()
          .filter(|stage| stage.kind == "link" || stage.kind == "runtime")
          .map(|stage| stage.id.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();
  let runtime_dependencies = snapshot
    .dependency_graph
    .runtime_artifacts
    .iter()
    .map(|artifact| {
      json!({
        "id": artifact.id,
        "kind": "dependency-runtime-artifact",
        "name": artifact.name,
        "artifact_kind": artifact.kind,
        "status": artifact.status,
        "depends_on": snapshot
          .build_plan
          .toolchain
          .stages
          .iter()
          .filter(|stage| stage.kind == "runtime")
          .map(|stage| stage.id.clone())
          .collect::<Vec<_>>(),
      })
    })
    .collect::<Vec<_>>();

  json!({
    "schemaVersion": 1,
    "caseId": snapshot.case.case_id,
    "projectPath": project.display().to_string(),
    "state": snapshot.build_plan.state,
    "nodes": {
      "toolchainStages": toolchain_stages,
      "functionBoundaries": function_boundaries,
      "cfgFunctions": cfg_functions,
      "typeCandidates": type_candidates,
      "dependencyImports": dependency_imports,
      "runtimeDependencies": runtime_dependencies,
      "translationUnits": translation_units,
      "linkUnits": link_units,
    },
    "edges": {
      "compileToObject": snapshot
        .build_plan
        .build_units
        .iter()
        .map(|unit| {
          json!({
            "from": unit.source_path,
            "to": unit.object_path,
            "status": unit.status,
          })
        })
        .collect::<Vec<_>>(),
      "objectToProof": snapshot
        .build_plan
        .build_units
        .iter()
        .map(|unit| {
          json!({
            "from": unit.object_path,
            "to": unit.proof_target,
            "status": unit.proof_target_status,
          })
        })
        .collect::<Vec<_>>(),
      "dependencyToLink": snapshot
        .dependency_graph
        .link_requirements
        .iter()
        .map(|requirement| {
          json!({
            "from": requirement.id,
            "to": snapshot.build_plan.link_plan.artifact_path,
            "status": requirement.status,
          })
        })
        .collect::<Vec<_>>(),
    },
  })
}

fn build_manifest_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  json!({
    "schemaVersion": 1,
    "caseId": snapshot.case.case_id,
    "projectPath": project.display().to_string(),
    "state": snapshot.build_plan.state,
    "generator": "mizuchi-rust-orchestrator",
    "target": {
      "format": snapshot.build_plan.target_format,
      "architecture": snapshot.build_plan.target_architecture,
      "platformFingerprint": {
        "objectFormat": snapshot.analysis.target.platform_fingerprint.object_format,
        "pointerWidthBits": snapshot.analysis.target.platform_fingerprint.pointer_width_bits,
        "vendor": snapshot.analysis.target.platform_fingerprint.vendor,
        "operatingSystem": snapshot.analysis.target.platform_fingerprint.operating_system,
        "environment": snapshot.analysis.target.platform_fingerprint.environment,
        "binaryInterfaceHypotheses": snapshot
          .analysis
          .target
          .platform_fingerprint
          .binary_interface_hypotheses,
        "tripleCandidates": snapshot
          .analysis
          .target
          .platform_fingerprint
          .triple_candidates,
      },
      "artifact": snapshot.build_plan.expected_artifact.path,
      "comparator": snapshot.build_plan.expected_artifact.comparator,
    },
    "toolchain": {
      "compiler": snapshot.build_plan.toolchain.compiler,
      "linker": snapshot.build_plan.toolchain.linker,
      "rankingStatus": snapshot.build_plan.toolchain.ranking_status,
      "recommendedProfile": snapshot.build_plan.toolchain.recommended_profile,
      "selectedProfile": snapshot.build_plan.toolchain.selected_profile,
      "invocationLedger": "compiler-invocation.json",
      "invocationStatus": snapshot.compiler_invocation.status,
      "invocationCandidates": snapshot.compiler_invocation.candidate_count,
      "recoveredInvocations": snapshot.compiler_invocation.recovered_invocation_count,
      "candidateProfiles": snapshot
        .build_plan
        .toolchain
        .candidate_profiles
        .iter()
        .map(|profile| {
          json!({
            "id": profile.id,
            "family": profile.family,
            "vendor": profile.vendor,
            "status": profile.status,
            "evidenceScore": profile.evidence_score,
            "evidenceConfidence": profile.evidence_confidence,
            "rankingReasons": profile.ranking_reasons,
          })
        })
        .collect::<Vec<_>>(),
      "stages": snapshot
        .build_plan
        .toolchain
        .stages
        .iter()
        .map(|stage| {
          json!({
            "id": stage.id,
            "kind": stage.kind,
            "status": stage.status,
            "requiredComponents": stage.required_components,
          })
        })
        .collect::<Vec<_>>(),
    },
    "buildSystem": {
      "kind": snapshot.build_plan.build_system.kind,
      "executable": snapshot.build_plan.build_system.executable,
      "reason": snapshot.build_plan.build_system.reason,
      "preferredBackend": snapshot.build_plan.build_system.preferred_backend,
      "candidateBackends": snapshot
        .build_plan
        .build_system
        .candidate_backends
        .iter()
        .map(|backend| {
          json!({
            "id": backend.id,
            "family": backend.family,
            "generator": backend.generator,
            "status": backend.status,
            "evidence": backend.evidence,
            "blockers": backend.blockers,
          })
        })
        .collect::<Vec<_>>(),
      "generatedArtifacts": snapshot
        .build_plan
        .build_system
        .generated_artifacts
        .iter()
        .map(|artifact| {
          json!({
            "path": artifact.path,
            "kind": artifact.kind,
            "backend": artifact.backend,
            "executable": artifact.executable,
            "detail": artifact.detail,
          })
        })
        .collect::<Vec<_>>(),
    },
    "translationUnits": snapshot
      .build_plan
      .build_units
      .iter()
      .map(|unit| {
        json!({
          "id": unit.id,
          "sourcePath": unit.source_path,
          "objectPath": unit.object_path,
          "language": unit.language,
          "proofTarget": unit.proof_target,
          "proofTargetStatus": unit.proof_target_status,
          "proofTargetLocator": unit.proof_target_locator,
          "proofSourcePath": unit.proof_source_path,
          "requiredInputs": unit.required_inputs,
          "blockers": unit.blockers,
        })
      })
      .collect::<Vec<_>>(),
    "linkPlan": {
      "artifactPath": snapshot.build_plan.link_plan.artifact_path,
      "kind": snapshot.build_plan.link_plan.kind,
      "status": snapshot.build_plan.link_plan.status,
      "linkerProfileCandidates": snapshot.build_plan.link_plan.linker_profile_candidates,
      "inputs": snapshot
        .build_plan
        .link_plan
        .inputs
        .iter()
        .map(|input| {
          json!({
            "name": input.name,
            "kind": input.kind,
            "source": input.source,
            "status": input.status,
          })
        })
        .collect::<Vec<_>>(),
      "runtimeArtifacts": snapshot
        .build_plan
        .link_plan
        .runtime_artifacts
        .iter()
        .map(|artifact| {
          json!({
            "name": artifact.name,
            "kind": artifact.kind,
            "status": artifact.status,
            "detail": artifact.detail,
          })
        })
        .collect::<Vec<_>>(),
      "blockers": snapshot.build_plan.link_plan.blockers,
    },
  })
}

fn toolchain_manifest_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  let upstream_evidence = collect_upstream_evidence(snapshot);
  let executable_components = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .flat_map(|profile| {
      profile.required_components.iter().filter_map(move |component| {
        let command_candidates = command_candidates_for_component(&component.name);
        let installed_candidates = probe_component_command_availability(&component.name)
          .into_iter()
          .map(|candidate| serde_json::to_value(candidate).expect("command availability serializes"))
          .collect::<Vec<_>>();
        matches!(component.kind.as_str(), "compiler" | "assembler" | "linker").then(|| {
          json!({
            "profile": profile.id,
            "name": component.name,
            "kind": component.kind,
            "status": component.status,
            "detail": component.detail,
            "commandCandidates": command_candidates,
            "installedCandidates": installed_candidates,
            "available": installed_candidates.iter().any(|item| item["installed"].as_bool() == Some(true)),
          })
        })
      })
    })
    .collect::<Vec<_>>();
  let resolved_executable_count = executable_components
    .iter()
    .flat_map(|component| component["installedCandidates"].as_array().into_iter().flatten())
    .filter(|candidate| candidate["installed"].as_bool() == Some(true))
    .count();
  let version_fingerprint_count = executable_components
    .iter()
    .flat_map(|component| component["installedCandidates"].as_array().into_iter().flatten())
    .filter(|candidate| candidate["versionOutput"].is_string())
    .count();

  let runtime_ownership = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .flat_map(|profile| {
      profile.required_components.iter().filter_map(move |component| {
        matches!(
          component.kind.as_str(),
          "runtime" | "libraries" | "abi" | "configuration"
        )
        .then(|| {
          json!({
            "profile": profile.id,
            "name": component.name,
            "kind": component.kind,
            "status": component.status,
            "detail": component.detail,
          })
        })
      })
    })
    .collect::<Vec<_>>();

  let stage_coverage = snapshot
    .build_plan
    .toolchain
    .stages
    .iter()
    .map(|stage| {
      json!({
        "id": stage.id,
        "name": stage.name,
        "kind": stage.kind,
        "status": stage.status,
        "requiredComponents": stage.required_components,
        "candidateProfiles": stage.candidate_profiles,
        "coverage": if stage.required_components.is_empty() { "none" } else { "partial" },
      })
    })
    .collect::<Vec<_>>();

  json!({
    "schemaVersion": 1,
    "caseId": snapshot.case.case_id,
    "projectPath": project.display().to_string(),
    "generator": "mizuchi-rust-orchestrator",
    "platformFingerprint": {
      "objectFormat": snapshot.analysis.target.platform_fingerprint.object_format,
      "pointerWidthBits": snapshot.analysis.target.platform_fingerprint.pointer_width_bits,
      "vendor": snapshot.analysis.target.platform_fingerprint.vendor,
      "operatingSystem": snapshot.analysis.target.platform_fingerprint.operating_system,
      "environment": snapshot.analysis.target.platform_fingerprint.environment,
      "tripleCandidates": snapshot.analysis.target.platform_fingerprint.triple_candidates,
    },
    "rankingStatus": snapshot.build_plan.toolchain.ranking_status,
    "recommendedProfile": snapshot.build_plan.toolchain.recommended_profile,
    "selectedProfile": snapshot.build_plan.toolchain.selected_profile,
    "invocationLedger": "compiler-invocation.json",
    "invocationStatus": snapshot.compiler_invocation.status,
    "invocationCandidates": snapshot.compiler_invocation.candidate_count,
    "recoveredInvocations": snapshot.compiler_invocation.recovered_invocation_count,
    "hostResolutionSummary": {
      "resolvedExecutableCount": resolved_executable_count,
      "versionFingerprintCount": version_fingerprint_count,
      "versionProbeEnabled": std::env::var("DECOMP_PROBE_TOOL_VERSIONS").ok().map(|value| {
        let normalized = value.trim().to_ascii_lowercase();
        !normalized.is_empty() && normalized != "0" && normalized != "false" && normalized != "no"
      }).unwrap_or(false),
    },
    "candidateProfiles": snapshot
      .build_plan
      .toolchain
      .candidate_profiles
      .iter()
      .map(|profile| {
        json!({
          "id": profile.id,
          "family": profile.family,
          "vendor": profile.vendor,
          "status": profile.status,
          "evidenceScore": profile.evidence_score,
          "evidenceConfidence": profile.evidence_confidence,
          "rankingReasons": profile.ranking_reasons,
          "upstreamEvidence": profile.upstream_evidence,
        })
      })
      .collect::<Vec<_>>(),
    "executableComponents": executable_components,
    "runtimeOwnership": runtime_ownership,
    "stageCoverage": stage_coverage,
    "upstreamEvidence": upstream_evidence,
    "blockers": snapshot.build_plan.blockers,
  })
}

fn upstream_evidence_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  let references = collect_upstream_evidence(snapshot);
  let validation = validate_upstream_evidence_catalog(&references);
  let profile_coverage = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .map(|profile| {
      let count = references
        .iter()
        .filter(|reference| reference.applies_to_profiles.iter().any(|item| item == &profile.id))
        .count();
      json!({
        "profile": profile.id,
        "referenceCount": count,
      })
    })
    .collect::<Vec<_>>();
  let systems = references
    .iter()
    .map(|reference| reference.system.clone())
    .collect::<BTreeSet<_>>()
    .into_iter()
    .collect::<Vec<_>>();
  let verification_modes = references
    .iter()
    .map(|reference| reference.verification.clone())
    .collect::<BTreeSet<_>>()
    .into_iter()
    .collect::<Vec<_>>();
  let validation_statuses = validation
    .entries
    .iter()
    .map(|entry| entry.status.clone())
    .collect::<BTreeSet<_>>()
    .into_iter()
    .collect::<Vec<_>>();
  let references = references
    .iter()
    .zip(validation.entries.iter())
    .map(|(reference, validation)| {
      json!({
        "system": reference.system,
        "role": reference.role,
        "appliesToProfiles": reference.applies_to_profiles,
        "repo": reference.repo,
        "path": reference.path,
        "revision": reference.revision,
        "sourceKind": reference.source_kind,
        "sourceSha": reference.source_sha,
        "apiUrl": reference.api_url,
        "gitUrl": reference.git_url,
        "htmlUrl": reference.html_url,
        "downloadUrl": reference.download_url,
        "verification": reference.verification,
        "rationale": reference.rationale,
        "rustPortStatus": reference.rust_port_status,
        "validationStatus": validation.status,
        "matchedCatalogSource": validation.matched_catalog_source,
        "resolvedSourceSha": validation.resolved_source_sha,
        "resolvedHtmlUrl": validation.resolved_html_url,
        "resolvedDownloadUrl": validation.resolved_download_url,
        "validationEvidence": validation.evidence,
      })
    })
    .collect::<Vec<_>>();
  json!({
    "schemaVersion": 1,
    "caseId": snapshot.case.case_id,
    "projectPath": project.display().to_string(),
    "generator": "mizuchi-rust-orchestrator",
    "purpose": "Typed upstream source-code references that ground analysis, proof, and target-model boundaries.",
    "catalogStatus": validation.catalog_status,
    "systems": systems,
    "verificationModes": verification_modes,
    "validationStatuses": validation_statuses,
    "profileCoverage": profile_coverage,
    "validation": {
      "mode": validation.mode,
      "ghAvailable": validation.gh_available,
      "validatedReferenceCount": validation.validated_reference_count,
      "matchedReferenceCount": validation.matched_reference_count,
      "driftedReferenceCount": validation.drifted_reference_count,
      "missingReferenceCount": validation.missing_reference_count,
      "errorCount": validation.error_count,
      "evidence": validation.evidence,
    },
    "references": references,
  })
}

fn attempt_matrix_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  let proof_targets = derive_proof_target_ledger(snapshot);
  let proof_target_available = proof_targets.mapped_unit_count > 0;
  let compile_log = project.join("build/compile.log");
  let candidate_object = project.join(&snapshot.build_plan.candidate_object);
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
  let attempt_plan = derive_attempt_plan(snapshot);
  let rows = attempt_plan
    .rows
    .iter()
    .map(|row| {
      let profile = snapshot
        .build_plan
        .toolchain
        .candidate_profiles
        .iter()
        .find(|profile| profile.id == row.profile_id)
        .expect("attempt profile must exist");
      let backend = snapshot
        .build_plan
        .build_system
        .candidate_backends
        .iter()
        .find(|backend| backend.id == row.backend_id)
        .expect("attempt backend must exist");

      let executable_components = profile
        .required_components
        .iter()
        .filter(|component| matches!(component.kind.as_str(), "compiler" | "assembler" | "linker"))
        .map(|component| {
          let command_candidates = command_candidates_for_component(&component.name);
          let installed_candidates = probe_component_command_availability(&component.name);
          let available = installed_candidates.iter().any(|candidate| candidate.installed);
          json!({
            "name": component.name,
            "kind": component.kind,
            "status": component.status,
            "commandCandidates": command_candidates,
            "installedCandidates": installed_candidates,
            "available": available,
          })
        })
        .collect::<Vec<_>>();

      let mut blockers = Vec::new();
      blockers.extend(
        executable_components
          .iter()
          .filter(|component| component["available"].as_bool() != Some(true))
          .map(|component| {
            format!(
              "{}:{} host candidate executables missing",
              component["name"].as_str().unwrap_or("unknown"),
              component["kind"].as_str().unwrap_or("unknown")
            )
          }),
      );
      blockers.extend(profile.uncertainty.iter().cloned());
      blockers.extend(backend.blockers.iter().cloned());
      if !proof_target_available {
        blockers.push("Golden proof target is unresolved.".to_string());
      }
      blockers.push(
        "Exact compiler/linker invocation is unresolved and intentionally not fabricated."
          .to_string(),
      );
      blockers.sort();
      blockers.dedup();

      let mut evidence = vec![
        format!("profile={}", row.profile_id),
        format!("backend={}", row.backend_id),
        format!("host_ready={}", row.host_ready),
        format!("proof_target_available={proof_target_available}"),
        format!("rebuild_requested={}", snapshot.actions.rebuild),
        format!("verify_requested={}", snapshot.actions.verify),
        format!("match_requested={}", snapshot.actions.match_requested),
      ];
      if compile_log.is_file() {
        evidence.push(format!("compile_log={}", compile_log.display()));
      }
      if candidate_object.is_file() {
        evidence.push(format!("candidate_object={}", candidate_object.display()));
      }
      if let Some(check) = rebuild_check {
        evidence.push(format!("rebuild_check={}({})", check.status, check.detail));
      }
      if let Some(check) = object_match_check {
        evidence.push(format!("object_match_check={}({})", check.status, check.detail));
      }

      json!({
        "id": row.id,
        "profileId": row.profile_id,
        "profileScore": row.profile_score,
        "profileConfidence": row.profile_confidence,
        "backendId": row.backend_id,
        "profileFamily": row.profile_family,
        "backendFamily": row.backend_family,
        "backendGenerator": row.backend_generator,
        "role": row.role,
        "rowStatus": row.row_status,
        "statusReason": row.status_reason,
        "hostStatus": row.host_status,
        "hostReady": row.host_ready,
        "proofStatus": row.proof_status,
        "rebuildStatus": row.rebuild_status,
        "exactInvocationStatus": row.exact_invocation_status,
        "priority": row.priority,
        "priorityClass": row.priority_class,
        "priorityReasons": row.priority_reasons,
        "nextAction": row.next_action,
        "candidateSource": snapshot.build_plan.candidate_source,
        "candidateObject": snapshot.build_plan.candidate_object,
        "proofTarget": snapshot.build_plan.proof_target,
        "executableComponents": executable_components,
        "runtimeRequirements": profile
          .required_components
          .iter()
          .filter(|component| !matches!(component.kind.as_str(), "compiler" | "assembler" | "linker"))
          .map(|component| {
            json!({
              "name": component.name,
              "kind": component.kind,
              "status": component.status,
            })
          })
          .collect::<Vec<_>>(),
        "evidence": evidence,
        "blockers": blockers,
      })
    })
    .collect::<Vec<_>>();

  let top_rows = attempt_plan
    .top_attempts
    .iter()
    .map(|row| {
      json!({
        "id": row.id,
        "rowStatus": row.row_status,
        "priority": row.priority,
        "priorityClass": row.priority_class,
        "nextAction": row.next_action,
      })
    })
    .collect::<Vec<_>>();
  let next_actions = attempt_plan
    .next_actions
    .iter()
    .map(|item| {
      json!({
        "priority": item.priority,
        "action": item.action,
      })
    })
    .collect::<Vec<_>>();

  json!({
    "schemaVersion": 1,
    "caseId": snapshot.case.case_id,
    "projectPath": project.display().to_string(),
    "generator": "mizuchi-rust-orchestrator",
    "summary": {
      "rows": rows.len(),
      "hostReadyRows": attempt_plan.host_ready_count,
      "actionableRows": attempt_plan.actionable_attempt_count,
      "rankingStatus": snapshot.build_plan.toolchain.ranking_status,
      "recommendedProfile": snapshot.build_plan.toolchain.recommended_profile,
      "selectedProfile": snapshot.build_plan.toolchain.selected_profile,
      "rebuildRequested": snapshot.actions.rebuild,
      "verifyRequested": snapshot.actions.verify,
      "matchRequested": snapshot.actions.match_requested,
      "proofTargetAvailable": proof_target_available,
      "priorityOrdered": true,
      "topRows": top_rows,
      "nextActions": next_actions,
    },
    "rows": rows,
  })
}

fn collect_upstream_evidence(snapshot: &ProjectSnapshot) -> Vec<crate::model::UpstreamSourceEvidence> {
  let mut merged = BTreeMap::new();

  for reference in snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .flat_map(|profile| profile.upstream_evidence.iter().cloned())
  {
    let key = vec![
      reference.system.clone(),
      reference.role.clone(),
      reference.repo.clone(),
      reference.path.clone(),
      reference.revision.clone(),
      reference.source_kind.clone(),
      reference.source_sha.clone(),
      reference.api_url.clone(),
      reference.git_url.clone(),
      reference.html_url.clone(),
      reference.download_url.clone(),
      reference.verification.clone(),
      reference.rationale.clone(),
      reference.rust_port_status.clone(),
    ];
    merged
      .entry(key)
      .and_modify(|existing: &mut crate::model::UpstreamSourceEvidence| {
        existing
          .applies_to_profiles
          .extend(reference.applies_to_profiles.iter().cloned());
        existing.applies_to_profiles.sort();
        existing.applies_to_profiles.dedup();
      })
      .or_insert(reference);
  }

  merged.into_values().collect()
}

fn validate_upstream_evidence_catalog(
  references: &[crate::model::UpstreamSourceEvidence],
) -> UpstreamEvidenceCatalogValidation {
  let validation_enabled = env_flag_enabled("DECOMP_VALIDATE_UPSTREAM_SOURCES");
  let gh_available = command_exists_normalized("gh");
  let mode = if validation_enabled {
    "gh-cli".to_string()
  } else {
    "catalog-only".to_string()
  };
  let mut entries = Vec::with_capacity(references.len());
  let mut validated_reference_count = 0;
  let mut matched_reference_count = 0;
  let mut drifted_reference_count = 0;
  let mut missing_reference_count = 0;
  let mut error_count = 0;

  for reference in references {
    let entry = validate_upstream_evidence_reference(reference, validation_enabled, gh_available);
    match entry.status.as_str() {
      "matched" => {
        validated_reference_count += 1;
        matched_reference_count += 1;
      }
      "drifted" => {
        validated_reference_count += 1;
        drifted_reference_count += 1;
      }
      "missing" => {
        validated_reference_count += 1;
        missing_reference_count += 1;
      }
      "error" => {
        validated_reference_count += 1;
        error_count += 1;
      }
      _ => {}
    }
    entries.push(entry);
  }

  let catalog_status = if !validation_enabled {
    "curated-gh-reference-catalog".to_string()
  } else if !gh_available {
    "gh-cli-unavailable".to_string()
  } else if error_count > 0 {
    "gh-cli-validation-errors".to_string()
  } else if drifted_reference_count > 0 || missing_reference_count > 0 {
    "gh-cli-validation-drift".to_string()
  } else {
    "gh-cli-validated".to_string()
  };

  let mut evidence = vec![
    format!("mode={mode}"),
    format!("gh_available={gh_available}"),
    format!("validation_enabled={validation_enabled}"),
    format!("reference_count={}", references.len()),
    format!("validated_reference_count={validated_reference_count}"),
  ];
  if !validation_enabled {
    evidence.push(
      "Set DECOMP_VALIDATE_UPSTREAM_SOURCES=1 to validate GitHub-backed references with gh."
        .to_string(),
    );
  }

  UpstreamEvidenceCatalogValidation {
    mode,
    gh_available,
    catalog_status,
    validated_reference_count,
    matched_reference_count,
    drifted_reference_count,
    missing_reference_count,
    error_count,
    entries,
    evidence,
  }
}

fn validate_upstream_evidence_reference(
  reference: &crate::model::UpstreamSourceEvidence,
  validation_enabled: bool,
  gh_available: bool,
) -> UpstreamEvidenceValidationEntry {
  let mut evidence = Vec::new();
  if let Some(command) = gh_revalidation_command(reference) {
    evidence.push(format!("revalidate_with={command}"));
  }

  if reference.source_kind != "github-content-file" {
    evidence.push(format!("source_kind={}", reference.source_kind));
    return UpstreamEvidenceValidationEntry {
      status: "source-unavailable".to_string(),
      matched_catalog_source: None,
      resolved_source_sha: None,
      resolved_html_url: None,
      resolved_download_url: None,
      evidence,
    };
  }

  if !validation_enabled {
    evidence.push("validation_mode=catalog-only".to_string());
    return UpstreamEvidenceValidationEntry {
      status: "not-run".to_string(),
      matched_catalog_source: None,
      resolved_source_sha: None,
      resolved_html_url: None,
      resolved_download_url: None,
      evidence,
    };
  }

  if !gh_available {
    evidence.push("gh_available=false".to_string());
    return UpstreamEvidenceValidationEntry {
      status: "tool-unavailable".to_string(),
      matched_catalog_source: None,
      resolved_source_sha: None,
      resolved_html_url: None,
      resolved_download_url: None,
      evidence,
    };
  }

  let Some(api_endpoint) = reference
    .api_url
    .strip_prefix("https://api.github.com/")
    .map(ToString::to_string)
  else {
    evidence.push(format!("api_url={}", reference.api_url));
    return UpstreamEvidenceValidationEntry {
      status: "error".to_string(),
      matched_catalog_source: None,
      resolved_source_sha: None,
      resolved_html_url: None,
      resolved_download_url: None,
      evidence,
    };
  };

  evidence.push(format!("gh_endpoint={api_endpoint}"));
  let output = Command::new("gh")
    .arg("api")
    .arg(&api_endpoint)
    .env("GH_PAGER", "cat")
    .output();

  let output = match output {
    Ok(output) => output,
    Err(err) => {
      evidence.push(format!("gh_error={err}"));
      return UpstreamEvidenceValidationEntry {
        status: "error".to_string(),
        matched_catalog_source: None,
        resolved_source_sha: None,
        resolved_html_url: None,
        resolved_download_url: None,
        evidence,
      };
    }
  };

  if !output.status.success() {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    evidence.push(format!("gh_status={}", output.status));
    if !stderr.is_empty() {
      evidence.push(format!("gh_stderr={stderr}"));
    }
    let status = if stderr.contains("404") || stderr.to_ascii_lowercase().contains("not found") {
      "missing"
    } else {
      "error"
    };
    return UpstreamEvidenceValidationEntry {
      status: status.to_string(),
      matched_catalog_source: None,
      resolved_source_sha: None,
      resolved_html_url: None,
      resolved_download_url: None,
      evidence,
    };
  }

  let response: GhContentsResponse = match serde_json::from_slice(&output.stdout) {
    Ok(response) => response,
    Err(err) => {
      evidence.push(format!("gh_parse_error={err}"));
      return UpstreamEvidenceValidationEntry {
        status: "error".to_string(),
        matched_catalog_source: None,
        resolved_source_sha: None,
        resolved_html_url: None,
        resolved_download_url: None,
        evidence,
      };
    }
  };

  let path_matches = response.path.as_deref() == Some(reference.path.as_str());
  let sha_matches = response.sha.as_deref() == Some(reference.source_sha.as_str());
  let html_matches = response.html_url.as_deref() == Some(reference.html_url.as_str());
  let download_matches = response.download_url.as_deref() == Some(reference.download_url.as_str());
  let matched_catalog_source = path_matches && sha_matches && html_matches && download_matches;
  evidence.push(format!("path_matches={path_matches}"));
  evidence.push(format!("sha_matches={sha_matches}"));
  evidence.push(format!("html_matches={html_matches}"));
  evidence.push(format!("download_matches={download_matches}"));

  UpstreamEvidenceValidationEntry {
    status: if matched_catalog_source {
      "matched".to_string()
    } else {
      "drifted".to_string()
    },
    matched_catalog_source: Some(matched_catalog_source),
    resolved_source_sha: response.sha,
    resolved_html_url: response.html_url,
    resolved_download_url: response.download_url,
    evidence,
  }
}

fn gh_revalidation_command(reference: &crate::model::UpstreamSourceEvidence) -> Option<String> {
  reference
    .api_url
    .strip_prefix("https://api.github.com/")
    .map(|endpoint| format!("gh api {endpoint}"))
}

fn env_flag_enabled(name: &str) -> bool {
  std::env::var(name)
    .map(|value| {
      let normalized = value.trim().to_ascii_lowercase();
      !normalized.is_empty() && normalized != "0" && normalized != "false" && normalized != "no"
    })
    .unwrap_or(false)
}

fn generated_build_backend(snapshot: &ProjectSnapshot, backend: &str, artifact_path: &str) -> String {
  let profiles = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .map(|profile| profile.id.as_str())
    .collect::<Vec<_>>()
    .join(", ");
  let blockers = snapshot
    .build_plan
    .blockers
    .iter()
    .map(|blocker| format!("# blocker: {blocker}"))
    .collect::<Vec<_>>()
    .join("\n");

  match backend {
    "make" | "nmake" => format!(
      "# Mizuchi generated build sketch\n# case: {}\n# backend: {}\n# artifact: {}\n# executable: false\n# candidate_profiles: {}\n# {}\n{}\n",
      snapshot.case.case_id,
      backend,
      artifact_path,
      profiles,
      snapshot.build_plan.build_system.reason,
      blockers,
    ),
    "ninja" => format!(
      "# Mizuchi generated build sketch\n# case = {}\n# backend = {}\n# artifact = {}\n# executable = false\n# candidate_profiles = {}\n# {}\n{}\n",
      snapshot.case.case_id,
      backend,
      artifact_path,
      profiles,
      snapshot.build_plan.build_system.reason,
      blockers,
    ),
    "msbuild" => format!(
      "<!-- Mizuchi generated build sketch -->\n<!-- case: {} -->\n<!-- backend: {} -->\n<!-- artifact: {} -->\n<!-- executable: false -->\n<!-- candidate_profiles: {} -->\n<!-- {} -->\n{}\n",
      snapshot.case.case_id,
      backend,
      artifact_path,
      profiles,
      snapshot.build_plan.build_system.reason,
      snapshot
        .build_plan
        .blockers
        .iter()
        .map(|blocker| format!("<!-- blocker: {blocker} -->"))
        .collect::<Vec<_>>()
        .join("\n"),
    ),
    "xcodebuild" => format!(
      "// Mizuchi generated build sketch\n// case: {}\n// backend: {}\n// artifact: {}\n// executable: false\n// candidate_profiles: {}\n// {}\n{}\n",
      snapshot.case.case_id,
      backend,
      artifact_path,
      profiles,
      snapshot.build_plan.build_system.reason,
      snapshot
        .build_plan
        .blockers
        .iter()
        .map(|blocker| format!("// blocker: {blocker}"))
        .collect::<Vec<_>>()
        .join("\n"),
    ),
    _ => format!(
      "Mizuchi generated build sketch\ncase: {}\nbackend: {}\nartifact: {}\nexecutable: false\ncandidate_profiles: {}\nreason: {}\nblockers:\n{}\n",
      snapshot.case.case_id,
      backend,
      artifact_path,
      profiles,
      snapshot.build_plan.build_system.reason,
      snapshot
        .build_plan
        .blockers
        .iter()
        .map(|blocker| format!("- {blocker}"))
        .collect::<Vec<_>>()
        .join("\n"),
    ),
  }
}

fn objdiff_config(project: &Path, snapshot: &ProjectSnapshot) -> serde_json::Value {
  let build_plan = &snapshot.build_plan;
  let case = &snapshot.case;
  let reconstruction = &snapshot.reconstruction;
  let watch_patterns = candidate_watch_patterns(reconstruction);
  let source_candidates = reconstruction
    .source_candidates
    .iter()
    .map(|candidate| (candidate.path.as_str(), candidate))
    .collect::<BTreeMap<_, _>>();
  let units = build_plan
        .build_units
        .iter()
        .filter(|unit| unit.proof_target_status == "mapped")
        .map(|unit| {
          let candidate = source_candidates.get(unit.source_path.as_str());
          json!({
            "name": unit.id,
            "target_path": unit.proof_target,
            "base_path": unit.object_path,
            "metadata": {
              "complete": unit.status == "matched",
              "reverse_fn_order": false,
              "source_path": unit.source_path,
              "auto_generated": true,
              "progress_categories": ["reconstruction", "verification"],
              "case_id": case.case_id,
              "adapter": case.adapter.id,
              "source_candidate_kind": candidate.map(|item| item.kind.clone()).unwrap_or_else(|| "unknown".to_string()),
              "source_candidate_status": candidate.map(|item| item.status.clone()).unwrap_or_else(|| "unknown".to_string()),
              "build_unit_status": unit.status,
              "build_unit_language": unit.language,
              "proof_target_status": unit.proof_target_status,
              "proof_target_locator": unit.proof_target_locator,
              "proof_source_path": unit.proof_source_path,
              "project_path": project.display().to_string(),
            },
        "symbol_mappings": {
          "primary_symbol": case.symbol.name,
          "symbol_locator": case.symbol.locator,
        }
      })
    })
    .collect::<Vec<_>>();

  json!({
    "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
    "min_version": env!("CARGO_PKG_VERSION"),
    "custom_make": "./build-system/compile-unit.sh",
    "custom_args": [],
    "target_dir": "build",
    "base_dir": "build",
    "build_target": false,
    "build_base": false,
    "watch_patterns": watch_patterns,
    "ignore_patterns": ["build/**/*"],
    "progress_categories": [
      { "id": "reconstruction", "name": "Reconstruction" },
      { "id": "verification", "name": "Verification" }
    ],
    "scratch": {
      "platform": case.target.platform,
      "compiler": build_plan.toolchain.selected_profile.as_deref().unwrap_or(build_plan.toolchain.compiler.as_str()),
      "c_flags": build_plan
        .toolchain
        .candidate_profiles
        .first()
        .map(|profile| profile.uncertainty.join(" | ")),
      "ctx_path": case.load.context_path,
      "build_ctx": false,
    },
    "options": {
      "mizuchi_adapter": case.adapter.id,
      "mizuchi_target_format": build_plan.target_format,
      "mizuchi_target_architecture": build_plan.target_architecture,
      "mizuchi_verification_status": snapshot.verification.status,
      "mizuchi_match_score_status": snapshot.verification.match_score.status,
    },
    "units": units,
  })
}

fn objdiff_build_driver(snapshot: &ProjectSnapshot, _repo_root: &Path) -> String {
  let mut out = String::from(
    "#!/usr/bin/env bash\n\
set -euo pipefail\n\
\n\
usage() {\n\
  cat <<'EOF'\n\
Usage: build-system/compile-unit.sh <object.o>\n\
\n\
Project-local objdiff build wrapper generated by Mizuchi.\n\
It resolves an objdiff unit object path to its candidate source path.\n\
EOF\n\
}\n\
\n\
if [[ $# -lt 1 ]]; then\n\
  usage >&2\n\
  exit 2\n\
fi\n\
\n\
object_rel=\"$1\"\n\
source_rel=\"\"\n\
function_name=\"\"\n\
project_root=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n\
runtime_root=\"",
  );
  out.push_str(&shell_escape_double_quotes(
    &snapshot.build_plan.compiler_script_root,
  ));
  out.push_str(
    "\"\n\
\n\
compiler_template=$(cat <<'EOF_MIZUCHI_COMPILER'\n",
  );
  out.push_str(&snapshot.build_plan.compiler_script);
  out.push_str(
    "\nEOF_MIZUCHI_COMPILER\n\
)\n\
\n\
if [[ -z \"${compiler_template//[[:space:]]/}\" ]]; then\n\
  echo \"compile-unit: no compilerScript template is configured\" >&2\n\
  exit 2\n\
fi\n\
\n\
if [[ \"",
  );
  out.push_str(if snapshot.build_plan.rebuild_supported {
    "true"
  } else {
    "false"
  });
  out.push_str(
    "\" != \"true\" ]]; then\n\
  echo \"compile-unit: compilerScript is configured but not executable proof yet\" >&2\n\
  exit 1\n\
fi\n\
\n\
case \"$object_rel\" in\n",
  );
  for unit in &snapshot.build_plan.build_units {
    out.push_str(&format!(
      "  \"{object}\") source_rel=\"{source}\"; function_name=\"{function_name}\" ;;\n",
      object = shell_escape_case_pattern(&unit.object_path),
      source = shell_escape_double_quotes(&unit.source_path),
      function_name = shell_escape_double_quotes(&unit.id),
    ));
  }
  out.push_str(
    "  *)\n\
    echo \"compile-unit: unknown build unit: ${object_rel}\" >&2\n\
    exit 2\n\
    ;;\n\
esac\n\
\n\
source_path=\"${project_root}/${source_rel}\"\n\
object_path=\"${project_root}/${object_rel}\"\n\
mkdir -p \"$(dirname \"$object_path\")\"\n\
\n\
expanded=\"$compiler_template\"\n\
expanded=\"${expanded//\\{\\{cFilePath\\}\\}/$source_path}\"\n\
expanded=\"${expanded//\\{\\{objFilePath\\}\\}/$object_path}\"\n\
expanded=\"${expanded//\\{\\{functionName\\}\\}/$function_name}\"\n\
\n\
cd \"$runtime_root\"\n\
exec bash -lc \"$expanded\"\n",
  );
  out
}

fn shell_escape_double_quotes(value: &str) -> String {
  value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn shell_escape_case_pattern(value: &str) -> String {
  shell_escape_double_quotes(value)
}

fn candidate_watch_patterns(reconstruction: &crate::model::ReconstructionGraph) -> Vec<String> {
  let mut patterns = BTreeSet::new();
  for candidate in &reconstruction.source_candidates {
    patterns.extend(candidate_watch_patterns_for_path(&candidate.path, &candidate.language));
  }
  if patterns.is_empty() {
    return reconstruction
      .project_structure
      .source_roots
      .iter()
      .map(|root| format!("{}/**/*", root.trim_end_matches('/')))
      .collect();
  }
  patterns.into_iter().collect()
}

fn candidate_watch_patterns_for_path(path: &str, language: &str) -> Vec<String> {
  let normalized = path.replace('\\', "/");
  if normalized.trim().is_empty() {
    return Vec::new();
  }

  let mut patterns = BTreeSet::new();
  patterns.insert(normalized.clone());

  let path = Path::new(&normalized);
  if let Some(parent) = path.parent().and_then(|parent| parent.to_str()) {
    let parent = parent.trim_end_matches('/');
    if !parent.is_empty() {
      patterns.insert(format!("{parent}/**/*"));
    }
  }

  let source_exts: &[&str] = match language {
    "c" => &["c", "h"],
    "c++" | "cpp" => &["cc", "cpp", "cxx", "c++", "hpp", "hh", "hxx", "h++"],
    "asm" => &["s", "S"],
    other => &[other],
  };

  if let Some(parent) = path.parent().and_then(|parent| parent.to_str()) {
    let parent = parent.trim_end_matches('/');
    if !parent.is_empty() {
      for ext in source_exts {
        patterns.insert(format!("{parent}/**/*.{}", ext));
      }
    }
  } else {
    for ext in source_exts {
      patterns.insert(format!("*.{}", ext));
    }
  }

  patterns.into_iter().collect()
}

fn expand_compiler_script_template(
  template: &str,
  source: &Path,
  object: &Path,
  function_name: &str,
) -> String {
  template
    .replace("{{cFilePath}}", &source.display().to_string())
    .replace("{{objFilePath}}", &object.display().to_string())
    .replace("{{functionName}}", function_name)
}

fn default_build_unit_proof_result(unit: &BuildUnitPlan) -> BuildUnitProofResult {
  BuildUnitProofResult {
    build_unit_id: unit.id.clone(),
    source_path: unit.source_path.clone(),
    object_path: unit.object_path.clone(),
    proof_target: unit.proof_target.clone(),
    proof_target_status: unit.proof_target_status.clone(),
    rebuild_status: "skipped".to_string(),
    rebuild_detail: "Rebuild has not been attempted for this build unit.".to_string(),
    object_match_status: "skipped".to_string(),
    object_match_detail: "Object match has not been attempted for this build unit.".to_string(),
    binary_diff_status: "skipped".to_string(),
    binary_diff_detail: "Native byte comparison has not been attempted for this build unit."
      .to_string(),
    artifact_comparison: None,
    evidence: Vec::new(),
  }
}

fn seed_build_unit_proof_results(
  build_plan: &BuildPlan,
  verification: &VerificationRecord,
) -> BTreeMap<String, BuildUnitProofResult> {
  let mut results = build_plan
    .build_units
    .iter()
    .map(|unit| (unit.id.clone(), default_build_unit_proof_result(unit)))
    .collect::<BTreeMap<_, _>>();
  for result in &verification.build_unit_proof_results {
    results.insert(result.build_unit_id.clone(), result.clone());
  }
  results
}

fn ordered_build_unit_proof_results(
  build_plan: &BuildPlan,
  results: &BTreeMap<String, BuildUnitProofResult>,
) -> Vec<BuildUnitProofResult> {
  build_plan
    .build_units
    .iter()
    .filter_map(|unit| results.get(&unit.id).cloned())
    .collect()
}

fn build_unit_compile_log_path(
  project: &Path,
  build_unit_count: usize,
  unit: &BuildUnitPlan,
) -> PathBuf {
  if build_unit_count <= 1 {
    project.join("build/compile.log")
  } else {
    project.join(format!(
      "build/logs/{}.compile.log",
      sanitize_identifier(&unit.id)
    ))
  }
}

fn resolve_build_unit_proof_target_path(
  project: &Path,
  repo_root: &Path,
  unit: &BuildUnitPlan,
) -> Option<PathBuf> {
  if unit.proof_target == "unavailable" {
    return None;
  }
  let proof_target_path = PathBuf::from(&unit.proof_target);
  if proof_target_path.is_absolute() {
    return Some(proof_target_path);
  }
  let project_relative = project.join(&proof_target_path);
  if project_relative.exists() {
    return Some(project_relative);
  }
  Some(resolve_path(repo_root, &unit.proof_target))
}

fn run_build_unit_rebuild(
  project: &Path,
  build_plan: &BuildPlan,
  unit: &BuildUnitPlan,
  function_name: &str,
) -> Result<BuildUnitProofResult> {
  let log_path = build_unit_compile_log_path(project, build_plan.build_units.len(), unit);
  if let Some(parent) = log_path.parent() {
    fs::create_dir_all(parent)?;
  }
  let source = project.join(&unit.source_path);
  let object = project.join(&unit.object_path);
  if let Some(parent) = object.parent() {
    fs::create_dir_all(parent)?;
  }

  let compiler_root = resolve_path(project, &build_plan.compiler_script_root);
  let mut result = default_build_unit_proof_result(unit);
  result.evidence.extend([
    format!("compiler_script_root={}", compiler_root.display()),
    format!("compile_log={}", log_path.display()),
    format!("function_name={function_name}"),
  ]);

  if !source.is_file() {
    result.rebuild_status = "failed".to_string();
    result.rebuild_detail = format!("Source candidate missing at {}", source.display());
    return Ok(result);
  }

  let expanded_script =
    expand_compiler_script_template(&build_plan.compiler_script, &source, &object, function_name);
  let output = Command::new("bash")
    .arg("-lc")
    .arg(&expanded_script)
    .current_dir(&compiler_root)
    .output()
    .with_context(|| format!("failed to execute compilerScript from {}", compiler_root.display()))?;
  let mut combined = output.stdout;
  combined.extend_from_slice(&output.stderr);
  fs::write(&log_path, combined)?;

  if output.status.success() {
    result.rebuild_status = "passed".to_string();
    result.rebuild_detail = format!("Candidate object written to {}", object.display());
  } else {
    result.rebuild_status = "failed".to_string();
    result.rebuild_detail = format!(
      "Configured compilerScript from {} did not produce a verified rebuild for {}.",
      compiler_root.display(),
      unit.id
    );
  }

  Ok(result)
}

fn format_archive_decimal(value: u64, width: usize) -> Option<String> {
  let text = value.to_string();
  (text.len() <= width).then(|| format!("{text:<width$}"))
}

#[derive(Debug)]
struct RawArchiveEntry {
  header_start: usize,
  data_start: usize,
  data_end: usize,
  next_start: usize,
  is_regular_member: bool,
  bsd_name_prefix_len: usize,
}

fn parse_archive_size(field: &[u8]) -> Result<usize> {
  let text = std::str::from_utf8(field)
    .context("archive member size field is not UTF-8")?
    .trim();
  text
    .parse::<usize>()
    .with_context(|| format!("archive member size field is invalid: {text}"))
}

fn raw_archive_name(field: &[u8]) -> Result<String> {
  let text = std::str::from_utf8(field).context("archive member name field is not UTF-8")?;
  Ok(text.trim_end().to_string())
}

fn raw_archive_regular_member(name: &str) -> bool {
  if name.is_empty()
    || name == "/"
    || name == "//"
    || name == "/SYM64/"
    || name == "/<ECSYMBOLS>/"
    || name.starts_with("__.SYMDEF")
  {
    return false;
  }
  if let Some(rest) = name.strip_prefix('/') {
    return rest.bytes().all(|byte| byte.is_ascii_digit());
  }
  true
}

fn raw_archive_bsd_name_prefix_len(name: &str) -> Result<usize> {
  let Some(rest) = name.strip_prefix("#1/") else {
    return Ok(0);
  };
  rest
    .trim()
    .parse::<usize>()
    .with_context(|| format!("archive BSD extended-name length is invalid: {name}"))
}

fn parse_raw_common_archive_entries(data: &[u8]) -> Result<Vec<RawArchiveEntry>> {
  if data.starts_with(b"!<thin>\n") {
    return Err(anyhow!("Thin archives cannot be materialized into standalone package proof yet."));
  }
  if !data.starts_with(b"!<arch>\n") {
    return Err(anyhow!("Archive package rebuild requires a common ar archive header."));
  }

  let mut entries = Vec::new();
  let mut offset = 8_usize;
  while offset < data.len() {
    if data.len().saturating_sub(offset) < 60 {
      return Err(anyhow!("Archive member header is truncated at offset {offset}."));
    }
    let header_start = offset;
    let header = &data[header_start..header_start + 60];
    if &header[58..60] != b"`\n" {
      return Err(anyhow!("Archive member header terminator is invalid at offset {offset}."));
    }
    let raw_name = raw_archive_name(&header[0..16])?;
    let size = parse_archive_size(&header[48..58])?;
    let data_start = header_start + 60;
    let data_end = data_start
      .checked_add(size)
      .ok_or_else(|| anyhow!("Archive member size overflows at offset {offset}."))?;
    if data_end > data.len() {
      return Err(anyhow!("Archive member data is truncated at offset {offset}."));
    }
    let mut next_start = data_end;
    if size % 2 != 0 {
      next_start = next_start
        .checked_add(1)
        .ok_or_else(|| anyhow!("Archive member padding overflows at offset {offset}."))?;
      if next_start > data.len() {
        return Err(anyhow!("Archive member padding is truncated at offset {offset}."));
      }
    }
    let bsd_name_prefix_len = raw_archive_bsd_name_prefix_len(&raw_name)?;
    if bsd_name_prefix_len > size {
      return Err(anyhow!(
        "Archive BSD extended-name prefix is larger than member data at offset {offset}."
      ));
    }
    entries.push(RawArchiveEntry {
      header_start,
      data_start,
      data_end,
      next_start,
      is_regular_member: raw_archive_regular_member(&raw_name),
      bsd_name_prefix_len,
    });
    offset = next_start;
  }

  Ok(entries)
}

fn archive_package_rebuild_blocker(
  repo_root: &Path,
  project: &Path,
  build_plan: &BuildPlan,
  analysis: &AnalysisRecord,
) -> Option<String> {
  if analysis.target.archive_members.is_empty() {
    return Some("Archive package rebuild requires member-backed archive analysis.".to_string());
  }
  if analysis.target.archive_is_thin.unwrap_or(false) {
    return Some("Thin archives cannot be materialized into standalone package proof yet.".to_string());
  }

  let target_archive = resolve_path(repo_root, &analysis.target.path);
  let target_bytes = match fs::read(&target_archive) {
    Ok(bytes) => bytes,
    Err(error) => {
      return Some(format!(
        "Archive package rebuild requires original archive bytes at {}: {}.",
        target_archive.display(),
        error
      ));
    }
  };
  let entries = match parse_raw_common_archive_entries(&target_bytes) {
    Ok(entries) => entries,
    Err(error) => return Some(error.to_string()),
  };
  let regular_count = entries
    .iter()
    .filter(|entry| entry.is_regular_member)
    .count();
  if regular_count != build_plan.build_units.len()
    || regular_count != analysis.target.archive_members.len()
  {
    return Some(format!(
      "Archive package rebuild requires raw archive members to align with analyzed build units; raw_regular_members={}, build_units={}, analyzed_members={}.",
      regular_count,
      build_plan.build_units.len(),
      analysis.target.archive_members.len()
    ));
  }

  let has_special_members = entries.iter().any(|entry| !entry.is_regular_member);
  if has_special_members {
    let mut unit_index = 0_usize;
    for entry in entries.iter().filter(|entry| entry.is_regular_member) {
      let unit = &build_plan.build_units[unit_index];
      let member_object = project.join(&unit.object_path);
      let member_bytes = match fs::read(&member_object) {
        Ok(bytes) => bytes,
        Err(error) => {
          return Some(format!(
            "Archive package rebuild requires rebuilt member {} at {}: {}.",
            unit.id,
            member_object.display(),
            error
          ));
        }
      };
      let original_payload_size = entry
        .data_end
        .saturating_sub(entry.data_start)
        .saturating_sub(entry.bsd_name_prefix_len);
      if member_bytes.len() != original_payload_size {
        return Some(format!(
          "Archive package rebuild preserves special members only when rebuilt member sizes are unchanged; unit={} original_payload_size={} rebuilt_size={}.",
          unit.id,
          original_payload_size,
          member_bytes.len()
        ));
      }
      unit_index += 1;
    }
  }
  None
}

fn write_archive_package(
  repo_root: &Path,
  project: &Path,
  build_plan: &BuildPlan,
  analysis: &AnalysisRecord,
) -> Result<()> {
  if let Some(blocker) = archive_package_rebuild_blocker(repo_root, project, build_plan, analysis) {
    return Err(anyhow!(blocker));
  }

  let target_archive = resolve_path(repo_root, &analysis.target.path);
  let target_bytes = fs::read(&target_archive)
    .with_context(|| format!("failed to read original archive {}", target_archive.display()))?;
  let entries = parse_raw_common_archive_entries(&target_bytes)?;
  let candidate_archive = project.join(&build_plan.candidate_object);
  if let Some(parent) = candidate_archive.parent() {
    fs::create_dir_all(parent)?;
  }

  let mut archive = Vec::new();
  archive.extend_from_slice(&target_bytes[0..8]);
  let mut unit_index = 0_usize;
  for entry in &entries {
    let mut header = target_bytes[entry.header_start..entry.data_start].to_vec();
    let mut payload = target_bytes[entry.data_start..entry.data_end].to_vec();
    if entry.is_regular_member {
      let unit = build_plan
        .build_units
        .get(unit_index)
        .ok_or_else(|| anyhow!("raw archive member count exceeded build units"))?;
      let member_object = project.join(&unit.object_path);
      let member_bytes = fs::read(&member_object)
        .with_context(|| format!("failed to read rebuilt archive member {}", member_object.display()))?;
      if entry.bsd_name_prefix_len == 0 {
        payload = member_bytes;
      } else {
        payload.truncate(entry.bsd_name_prefix_len);
        payload.extend_from_slice(&member_bytes);
      }
      let size = format_archive_decimal(payload.len() as u64, 10)
        .ok_or_else(|| anyhow!("rebuilt archive member {} is too large for ar header", unit.id))?;
      header[48..58].copy_from_slice(size.as_bytes());
      unit_index += 1;
    }
    archive.extend_from_slice(&header);
    archive.extend_from_slice(&payload);
    if payload.len() % 2 != 0 {
      let original_padding = target_bytes
        .get(entry.data_end)
        .copied()
        .unwrap_or(b'\n');
      archive.push(original_padding);
    }
    debug_assert!(entry.next_start <= target_bytes.len());
  }

  fs::write(&candidate_archive, archive)
    .with_context(|| format!("failed to write {}", candidate_archive.display()))?;
  Ok(())
}

fn run_rebuild(
  repo_root: &Path,
  project: &Path,
  build_plan: &BuildPlan,
  analysis: &AnalysisRecord,
  function_name: &str,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) -> Result<()> {
  let compiler_root = resolve_path(project, &build_plan.compiler_script_root);
  let mut results = seed_build_unit_proof_results(build_plan, verification);
  let mut failed_units = Vec::new();

  for unit in &build_plan.build_units {
    let unit_function_name = if build_plan.build_units.len() == 1 {
      function_name
    } else {
      unit.id.as_str()
    };
    let result = run_build_unit_rebuild(project, build_plan, unit, unit_function_name)?;
    if result.rebuild_status != "passed" {
      failed_units.push(format!("{}: {}", unit.id, result.rebuild_detail));
    }
    results.insert(unit.id.clone(), result);
  }

  verification.build_unit_proof_results = ordered_build_unit_proof_results(build_plan, &results);

  if build_plan.build_units.len() > 1 {
    let summary = verification
      .build_unit_proof_results
      .iter()
      .map(|result| {
        format!(
          "{}\t{}\t{}\t{}",
          result.build_unit_id,
          result.rebuild_status,
          result.object_path,
          result.rebuild_detail
        )
      })
      .collect::<Vec<_>>()
      .join("\n");
    fs::write(project.join("build/compile.log"), format!("{summary}\n"))?;
  }

  if !failed_units.is_empty() {
    verification.add_failure(FailureClass::CompilerUnknown);
    verification.set_check(VerificationCheck::failed(
      "rebuild_proof",
      false,
      format!(
        "Configured compilerScript from {} did not produce verified rebuilds for {} build unit(s).",
        compiler_root.display(),
        failed_units.len()
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "compiler-unresolved",
      FailureClass::CompilerUnknown,
      "A target-specific compiler command and flags have not been recovered.",
      vec![
        format!("compiler_script_root={}", compiler_root.display()),
        format!("compiler_script_template={}", build_plan.compiler_script),
        format!("compile_log={}", project.join("build/compile.log").display()),
        format!("failed_build_units={}", failed_units.join(" | ")),
      ],
    ));
    return Ok(());
  }

  if !analysis.target.archive_members.is_empty() {
    match write_archive_package(repo_root, project, build_plan, analysis) {
      Ok(()) => {
        verification.set_check(VerificationCheck::passed(
          "rebuild_proof",
          false,
          format!(
            "Candidate objects written for {} build unit(s) and archive package written to {}.",
            verification.build_unit_proof_results.len(),
            project.join(&build_plan.candidate_object).display()
          ),
        ));
      }
      Err(error) => {
        verification.set_check(VerificationCheck::skipped(
          "rebuild_proof",
          false,
          format!(
            "Candidate objects were rebuilt for {} build unit(s), but top-level archive/package rebuild is unavailable: {}",
            verification.build_unit_proof_results.len(),
            error
          ),
        ));
        uncertainty.items.push(UncertaintyItem::blocking(
          "archive-package-unresolved",
          FailureClass::SemanticUnknown,
          "Archive/package rebuild is not yet reproducible for this archive layout.",
          vec![
            format!(
              "candidate_archive={}",
              project.join(&build_plan.candidate_object).display()
            ),
            format!(
              "archive_kind={}",
              analysis.target.archive_kind.as_deref().unwrap_or("unknown")
            ),
            error.to_string(),
          ],
        ));
      }
    }
  } else {
    verification.set_check(VerificationCheck::passed(
      "rebuild_proof",
      false,
      format!(
        "Candidate object written for {} build unit(s).",
        verification.build_unit_proof_results.len()
      ),
    ));
  }

  Ok(())
}

fn run_verify(
  repo_root: &Path,
  project: &Path,
  build_plan: &BuildPlan,
  analysis: &AnalysisRecord,
  verification: &mut VerificationRecord,
  uncertainty: &mut crate::model::UncertaintyLedger,
) -> Result<()> {
  let objdiff_available = crate::model::command_exists("objdiff");
  let mut results = seed_build_unit_proof_results(build_plan, verification);
  let mut mapped_unit_count = 0_usize;
  let mut object_match_passed_count = 0_usize;
  let mut comparison_available_count = 0_usize;
  let mut binary_diff_passed_count = 0_usize;
  let mut candidate_missing_units = Vec::new();
  let mut target_missing_units = Vec::new();
  let mut proof_unavailable_units = Vec::new();
  let mut object_mismatch_units = Vec::new();
  let mut binary_mismatch_units = Vec::new();
  let mut section_mismatch_units = Vec::new();
  let mut symbol_mismatch_units = Vec::new();
  let mut relocation_mismatch_units = Vec::new();

  for unit in &build_plan.build_units {
    let mut result = results
      .remove(&unit.id)
      .unwrap_or_else(|| default_build_unit_proof_result(unit));
    result.source_path = unit.source_path.clone();
    result.object_path = unit.object_path.clone();
    result.proof_target = unit.proof_target.clone();
    result.proof_target_status = unit.proof_target_status.clone();
    result.object_match_status = "skipped".to_string();
    result.object_match_detail = "Object match has not been attempted for this build unit.".to_string();
    result.binary_diff_status = "skipped".to_string();
    result.binary_diff_detail =
      "Native byte comparison has not been attempted for this build unit.".to_string();
    result.artifact_comparison = None;

    if unit.proof_target_status != "mapped" || unit.proof_target == "unavailable" {
      proof_unavailable_units.push(unit.id.clone());
      result.object_match_detail = "Per-build-unit proof target is unavailable.".to_string();
      result.binary_diff_detail =
        "Native byte comparison requires a mapped proof target.".to_string();
      results.insert(unit.id.clone(), result);
      continue;
    }

    mapped_unit_count += 1;
    let candidate = project.join(&unit.object_path);
    let Some(target) = resolve_build_unit_proof_target_path(project, repo_root, unit) else {
      proof_unavailable_units.push(unit.id.clone());
      result.object_match_detail = "Per-build-unit proof target could not be resolved.".to_string();
      result.binary_diff_detail =
        "Native byte comparison requires a resolved proof target.".to_string();
      results.insert(unit.id.clone(), result);
      continue;
    };

    if !candidate.is_file() {
      verification.add_failure(FailureClass::ProofArtifactMissing);
      candidate_missing_units.push(unit.id.clone());
      result.object_match_status = "failed".to_string();
      result.object_match_detail = format!("Candidate object missing at {}", candidate.display());
      result.binary_diff_status = "failed".to_string();
      result.binary_diff_detail = format!(
        "Native byte comparison requires a candidate object at {}.",
        candidate.display()
      );
      results.insert(unit.id.clone(), result);
      continue;
    }
    if !target.is_file() {
      verification.add_failure(FailureClass::ProofArtifactMissing);
      target_missing_units.push(unit.id.clone());
      result.object_match_status = "failed".to_string();
      result.object_match_detail = format!("Golden object missing at {}", target.display());
      result.binary_diff_status = "failed".to_string();
      result.binary_diff_detail = format!(
        "Native byte comparison requires a proof target at {}.",
        target.display()
      );
      results.insert(unit.id.clone(), result);
      continue;
    }

    if objdiff_available {
      let output = Command::new("objdiff")
        .arg("diff")
        .arg(&target)
        .arg(&candidate)
        .output()
        .context("failed to execute objdiff")?;
      let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
      if output.status.success() {
        object_match_passed_count += 1;
        result.object_match_status = "passed".to_string();
        result.object_match_detail = "objdiff reported a clean match.".to_string();
      } else {
        verification.add_failure(FailureClass::VerificationMismatch);
        object_mismatch_units.push(unit.id.clone());
        result.object_match_status = "failed".to_string();
        result.object_match_detail = if detail.is_empty() {
          "objdiff reported differences.".to_string()
        } else {
          detail
        };
      }
    } else {
      result.object_match_status = "failed".to_string();
      result.object_match_detail = "objdiff is not installed on PATH.".to_string();
    }

    let comparison = compare_artifacts(&target, &candidate)?;
    comparison_available_count += 1;
    if comparison.byte_equal {
      binary_diff_passed_count += 1;
      result.binary_diff_status = "passed".to_string();
      result.binary_diff_detail =
        "Native byte comparison reported identical artifacts.".to_string();
    } else {
      verification.add_failure(FailureClass::VerificationMismatch);
      binary_mismatch_units.push(unit.id.clone());
      result.binary_diff_status = "failed".to_string();
      result.binary_diff_detail = match comparison.first_mismatch_offset {
        Some(offset) => format!("Native byte comparison differed at offset {offset}."),
        None => "Native byte comparison reported differences.".to_string(),
      };
    }
    if !comparison.section_inventory_equal {
      section_mismatch_units.push(unit.id.clone());
    }
    if !comparison.symbol_inventory_equal {
      symbol_mismatch_units.push(unit.id.clone());
    }
    if !comparison.relocation_inventory_equal {
      relocation_mismatch_units.push(unit.id.clone());
    }
    result.evidence.extend([
      format!("candidate_path={}", candidate.display()),
      format!("proof_target_path={}", target.display()),
    ]);
    result.artifact_comparison = Some(comparison);
    results.insert(unit.id.clone(), result);
  }

  verification.build_unit_proof_results = ordered_build_unit_proof_results(build_plan, &results);

  if mapped_unit_count == 0 {
    verification.add_failure(FailureClass::ProofArtifactMissing);
    verification.set_check(VerificationCheck::failed(
      "object_match",
      false,
      "No per-build-unit proof target has been mapped for authoritative object proof.",
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "proof-target-missing",
      FailureClass::ProofArtifactMissing,
      "Object-level proof cannot run until at least one build unit has a mapped golden target object.",
      vec!["proof_target=unavailable".to_string()],
    ));
  } else if !objdiff_available {
    verification.add_failure(FailureClass::ToolMissing);
    verification.set_check(VerificationCheck::failed(
      "object_match",
      false,
      format!(
        "objdiff is not installed on PATH; {} mapped build unit(s) require authoritative object proof.",
        mapped_unit_count
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "objdiff-missing",
      FailureClass::ToolMissing,
      "objdiff is required for authoritative object matching.",
      vec!["command=objdiff".to_string()],
    ));
  } else if object_match_passed_count == mapped_unit_count {
    verification.set_check(VerificationCheck::passed(
      "object_match",
      false,
      format!(
        "objdiff reported clean matches for {} mapped build unit(s).",
        mapped_unit_count
      ),
    ));
  } else {
    if !candidate_missing_units.is_empty() || !target_missing_units.is_empty() {
      verification.add_failure(FailureClass::ProofArtifactMissing);
    } else {
      verification.add_failure(FailureClass::VerificationMismatch);
    }
    verification.set_check(VerificationCheck::failed(
      "object_match",
      false,
      format!(
        "objdiff matched {} of {} mapped build unit(s); mismatches={}, candidate_missing={}, proof_missing={}, proof_unavailable={}.",
        object_match_passed_count,
        mapped_unit_count,
        if object_mismatch_units.is_empty() {
          "none".to_string()
        } else {
          object_mismatch_units.join(", ")
        },
        if candidate_missing_units.is_empty() {
          "none".to_string()
        } else {
          candidate_missing_units.join(", ")
        },
        if target_missing_units.is_empty() {
          "none".to_string()
        } else {
          target_missing_units.join(", ")
        },
        if proof_unavailable_units.is_empty() {
          "none".to_string()
        } else {
          proof_unavailable_units.join(", ")
        },
      ),
    ));
  }

  verification.artifact_comparison = None;
  let single_unit_comparison = verification.build_unit_proof_results.first().cloned();
  let top_level_candidate = project.join(&build_plan.candidate_object);
  let top_level_target = if build_plan.proof_target == "unavailable" {
    None
  } else {
    Some(resolve_path(repo_root, &build_plan.proof_target))
  };
  let top_level_archive_comparison = if build_plan.expected_artifact.kind == "static-library"
    && top_level_candidate.is_file()
    && top_level_target.as_ref().is_some_and(|target| target.is_file())
  {
    Some(compare_artifacts(
      top_level_target
        .as_ref()
        .expect("checked target availability above"),
      &top_level_candidate,
    )?)
  } else {
    None
  };

  if build_plan.build_units.len() == 1 {
    if let Some(result) = single_unit_comparison {
      if let Some(comparison) = result.artifact_comparison.clone() {
        if result.binary_diff_status == "passed" {
          verification.set_check(VerificationCheck::passed(
            "binary_diff",
            false,
            result.binary_diff_detail.clone(),
          ));
        } else {
          verification.add_failure(FailureClass::VerificationMismatch);
          verification.set_check(VerificationCheck::failed(
            "binary_diff",
            false,
            result.binary_diff_detail.clone(),
          ));
        }
        verification.set_check(comparison_check(
          "section_comparison",
          comparison.section_inventory_equal,
          "section",
        ));
        verification.set_check(comparison_check(
          "symbol_comparison",
          comparison.symbol_inventory_equal,
          "symbol",
        ));
        verification.set_check(comparison_check(
          "relocation_comparison",
          comparison.relocation_inventory_equal,
          "relocation",
        ));
        verification.artifact_comparison = Some(comparison);
      } else {
        verification.set_check(VerificationCheck::skipped(
          "binary_diff",
          true,
          "Binary diffing requires both a golden target artifact and a rebuilt candidate artifact.",
        ));
        verification.set_check(VerificationCheck::skipped(
          "section_comparison",
          true,
          "Section comparison requires both a golden target artifact and a rebuilt candidate artifact.",
        ));
        verification.set_check(VerificationCheck::skipped(
          "symbol_comparison",
          true,
          "Symbol comparison requires both a golden target artifact and a rebuilt candidate artifact.",
        ));
        verification.set_check(VerificationCheck::skipped(
          "relocation_comparison",
          true,
          "Relocation comparison requires both a golden target artifact and a rebuilt candidate artifact.",
        ));
      }
    } else {
      verification.set_check(VerificationCheck::skipped(
        "binary_diff",
        true,
        "Binary diffing requires both a golden target artifact and a rebuilt candidate artifact.",
      ));
      verification.set_check(VerificationCheck::skipped(
        "section_comparison",
        true,
        "Section comparison requires both a golden target artifact and a rebuilt candidate artifact.",
      ));
      verification.set_check(VerificationCheck::skipped(
        "symbol_comparison",
        true,
        "Symbol comparison requires both a golden target artifact and a rebuilt candidate artifact.",
      ));
      verification.set_check(VerificationCheck::skipped(
        "relocation_comparison",
        true,
        "Relocation comparison requires both a golden target artifact and a rebuilt candidate artifact.",
      ));
    }
  } else if let Some(comparison) = top_level_archive_comparison {
    if comparison.byte_equal {
      verification.set_check(VerificationCheck::passed(
        "binary_diff",
        false,
        "Top-level archive/package byte comparison reported identical artifacts.",
      ));
    } else {
      verification.add_failure(FailureClass::VerificationMismatch);
      verification.set_check(VerificationCheck::failed(
        "binary_diff",
        false,
        match comparison.first_mismatch_offset {
          Some(offset) => format!("Top-level archive/package byte comparison differed at offset {offset}."),
          None => "Top-level archive/package byte comparison reported differences.".to_string(),
        },
      ));
    }
    verification.set_check(comparison_check(
      "section_comparison",
      comparison.section_inventory_equal,
      "section",
    ));
    verification.set_check(comparison_check(
      "symbol_comparison",
      comparison.symbol_inventory_equal,
      "symbol",
    ));
    verification.set_check(comparison_check(
      "relocation_comparison",
      comparison.relocation_inventory_equal,
      "relocation",
    ));
    verification.artifact_comparison = Some(comparison);
  } else if comparison_available_count == 0 {
    verification.set_check(VerificationCheck::skipped(
      "binary_diff",
      true,
      "Top-level binary diff is unavailable because no build-unit artifact pairs were comparable.",
    ));
    verification.set_check(VerificationCheck::skipped(
      "section_comparison",
      true,
      "Section comparison requires per-build-unit artifact comparisons or a rebuilt top-level artifact.",
    ));
    verification.set_check(VerificationCheck::skipped(
      "symbol_comparison",
      true,
      "Symbol comparison requires per-build-unit artifact comparisons or a rebuilt top-level artifact.",
    ));
    verification.set_check(VerificationCheck::skipped(
      "relocation_comparison",
      true,
      "Relocation comparison requires per-build-unit artifact comparisons or a rebuilt top-level artifact.",
    ));
  } else {
    if !candidate_missing_units.is_empty() || !target_missing_units.is_empty() {
      verification.add_failure(FailureClass::ProofArtifactMissing);
      verification.set_check(VerificationCheck::failed(
        "binary_diff",
        false,
        format!(
          "Native byte comparison is missing required artifacts for candidate_missing={} and proof_missing={}.",
          if candidate_missing_units.is_empty() {
            "none".to_string()
          } else {
            candidate_missing_units.join(", ")
          },
          if target_missing_units.is_empty() {
            "none".to_string()
          } else {
            target_missing_units.join(", ")
          }
        ),
      ));
    } else if !binary_mismatch_units.is_empty() {
      verification.add_failure(FailureClass::VerificationMismatch);
      verification.set_check(VerificationCheck::failed(
        "binary_diff",
        false,
        format!(
          "Native byte comparison differed for {} of {} compared build unit(s): {}.",
          binary_mismatch_units.len(),
          comparison_available_count,
          binary_mismatch_units.join(", ")
        ),
      ));
    } else {
      verification.set_check(VerificationCheck::skipped(
        "binary_diff",
        true,
        format!(
          "Per-build-unit native byte comparisons passed for {} mapped build unit(s), but top-level archive/package byte equivalence is not established.",
          binary_diff_passed_count
        ),
      ));
    }

    let aggregate_inventory_check = |name: &str,
                                     mismatched_units: &[String],
                                     label: &str|
     -> VerificationCheck {
      if !mismatched_units.is_empty() {
        VerificationCheck::failed(
          name,
          true,
          format!(
            "Per-build-unit {} inventory mismatched for: {}.",
            label,
            mismatched_units.join(", ")
          ),
        )
      } else {
        VerificationCheck::skipped(
          name,
          true,
          format!(
            "Per-build-unit {} inventories matched where comparable, but no top-level archive/package {} comparison is available.",
            label, label
          ),
        )
      }
    };
    verification.set_check(aggregate_inventory_check(
      "section_comparison",
      &section_mismatch_units,
      "section",
    ));
    verification.set_check(aggregate_inventory_check(
      "symbol_comparison",
      &symbol_mismatch_units,
      "symbol",
    ));
    verification.set_check(aggregate_inventory_check(
      "relocation_comparison",
      &relocation_mismatch_units,
      "relocation",
    ));
  }

  verification.set_check(VerificationCheck::passed(
    "binary_fingerprint",
    true,
    format!(
      "Captured input fingerprint: kind={}, arch={}, size={} bytes, sha256={}.",
      analysis.target.file_kind,
      analysis.target.architecture,
      analysis.target.size_bytes,
      analysis.target.sha256,
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "section_inventory",
    true,
    format!(
      "Captured {} section record(s) from native object parsing.",
      analysis.target.sections.len()
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "symbol_inventory",
    true,
    format!(
      "Captured {} symbol record(s) from native object parsing.",
      analysis.target.symbols.len()
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "function_boundary_inventory",
    true,
    format!(
      "Captured {} symbol-derived function boundary record(s); CFG recovery remains unproven.",
      analysis.target.functions.len()
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "relocation_inventory",
    true,
    format!(
      "Captured {} relocation record(s) from native object parsing.",
      analysis.target.relocations.len()
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "dependency_inventory",
    true,
    format!(
      "Captured {} import(s), {} export(s), and {} dynamic symbol record(s).",
      analysis.target.imports.len(),
      analysis.target.exports.len(),
      analysis.target.dynamic_symbols.len()
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "debug_inventory",
    true,
    format!(
      "Captured debug evidence: has_debug_symbols={}, build_id={}, debuglink={}, mach_uuid={}.",
      analysis.target.debug.has_debug_symbols,
      analysis.target.build_id.as_deref().unwrap_or("none"),
      analysis
        .target
        .debug
        .gnu_debuglink
        .as_ref()
        .map(|link| link.file.as_str())
        .unwrap_or("none"),
      analysis.target.debug.mach_uuid.as_deref().unwrap_or("none")
    ),
  ));
  verification.set_check(VerificationCheck::passed(
    "toolchain_fingerprint",
    true,
    format!(
      "Captured toolchain evidence: compiler={}, linker={}, comment_strings={}, note_sections={}.",
      analysis.target.toolchain.compiler,
      analysis.target.toolchain.linker,
      analysis.target.toolchain.comment_strings.len(),
      analysis.target.toolchain.notes.len()
    ),
  ));
  let mut missing_toolchain_components = Vec::new();
  let mut resolved_tool_count = 0_usize;
  let mut version_fingerprint_count = 0_usize;
  for profile in &build_plan.toolchain.candidate_profiles {
    for component in profile
      .required_components
      .iter()
      .filter(|component| matches!(component.kind.as_str(), "compiler" | "assembler" | "linker"))
    {
      let candidates = probe_component_command_availability(&component.name);
      resolved_tool_count += candidates.iter().filter(|candidate| candidate.installed).count();
      version_fingerprint_count += candidates
        .iter()
        .filter(|candidate| candidate.version_output.is_some())
        .count();
      let available = candidates.iter().any(|candidate| candidate.installed);
      if !available {
        missing_toolchain_components.push(format!(
          "{}:{}({})",
          profile.id, component.name, component.kind
        ));
      }
    }
  }
  if missing_toolchain_components.is_empty() {
    verification.set_check(VerificationCheck::passed(
      "toolchain_host_availability",
      true,
      format!(
        "At least one candidate executable was present for each compiler/assembler/linker component family; resolved_tool_candidates={}, version_fingerprints={}.",
        resolved_tool_count, version_fingerprint_count
      ),
    ));
  } else {
    verification.add_failure(FailureClass::ToolMissing);
    verification.set_check(VerificationCheck::failed(
      "toolchain_host_availability",
      true,
      format!(
        "Host is missing candidate toolchain executables for: {}.",
        missing_toolchain_components.join(", ")
      ),
    ));
    uncertainty.items.push(UncertaintyItem::blocking(
      "toolchain-host-missing",
      FailureClass::ToolMissing,
      "Host tool availability is insufficient for some candidate compiler families.",
      missing_toolchain_components,
    ));
  }
  verification.set_check(VerificationCheck::skipped(
    "cfg_comparison",
    true,
    "CFG comparison remains advisory until analyzer integration lands.",
  ));
  verification.set_check(VerificationCheck::skipped(
    "symbol_type_comparison",
    true,
    "Symbol/type comparison is emitted as evidence metadata only.",
  ));

  Ok(())
}

fn resolve_path(root: &Path, raw: &str) -> PathBuf {
  let candidate = PathBuf::from(raw);
  if candidate.is_absolute() {
    candidate
  } else {
    root.join(candidate)
  }
}

fn comparison_check(name: &str, matched: bool, label: &str) -> VerificationCheck {
  if matched {
    VerificationCheck::passed(
      name,
      true,
      format!("Native {label} inventory comparison matched."),
    )
  } else {
    VerificationCheck::failed(
      name,
      true,
      format!("Native {label} inventory comparison reported differences."),
    )
  }
}

fn artifact_fingerprint(path: &Path, bytes: &[u8]) -> Result<ArtifactFingerprint> {
  let parsed_kind = object::FileKind::parse(bytes).ok();
  let file_kind = parsed_kind
    .map(|kind| format!("{kind:?}"))
    .unwrap_or_else(|| "Unknown".to_string());
  let parsed = File::parse(bytes).ok();
  let archive = if matches!(parsed_kind, Some(object::FileKind::Archive)) {
    ArchiveFile::parse(bytes).ok()
  } else {
    None
  };

  let mut sections = parsed
    .as_ref()
    .map(|file| {
      file
        .sections()
        .map(|section| ComparableSection {
          name: section.name().unwrap_or("unknown").to_string(),
          size: section.size(),
          kind: format!("{:?}", section.kind()),
        })
        .collect::<Vec<_>>()
    })
    .or_else(|| {
      archive.as_ref().map(|archive| {
        archive
          .members()
          .filter_map(|member| member.ok())
          .map(|member| ComparableSection {
            name: String::from_utf8_lossy(member.name()).to_string(),
            size: member.size(),
            kind: if member.is_thin() {
              "ArchiveThinMember".to_string()
            } else {
              "ArchiveMember".to_string()
            },
          })
          .collect::<Vec<_>>()
      })
    })
    .unwrap_or_default();
  sections.sort_by(|left, right| {
    left
      .name
      .cmp(&right.name)
      .then(left.kind.cmp(&right.kind))
      .then(left.size.cmp(&right.size))
  });

  let mut symbols = parsed
    .as_ref()
    .map(|file| {
      file
        .symbols()
        .map(|symbol| ComparableSymbol {
          name: symbol.name().unwrap_or("unknown").to_string(),
          size: symbol.size(),
          kind: format!("{:?}", symbol.kind()),
          scope: format!("{:?}", symbol.scope()),
        })
        .collect::<Vec<_>>()
    })
    .unwrap_or_default();
  symbols.sort_by(|left, right| {
    left
      .name
      .cmp(&right.name)
      .then(left.kind.cmp(&right.kind))
      .then(left.scope.cmp(&right.scope))
      .then(left.size.cmp(&right.size))
  });

  let mut relocations = parsed
    .as_ref()
    .map(|file| comparable_relocations(file))
    .unwrap_or_default();
  relocations.sort_by(|left, right| {
    left
      .section
      .cmp(&right.section)
      .then(left.offset.cmp(&right.offset))
      .then(left.kind.cmp(&right.kind))
      .then(left.target.cmp(&right.target))
      .then(left.addend.cmp(&right.addend))
  });

  Ok(ArtifactFingerprint {
    path: path.display().to_string(),
    file_kind,
    size_bytes: bytes.len() as u64,
    sha256: hex::encode(Sha256::digest(bytes)),
    sections,
    symbols,
    relocations,
  })
}

fn comparable_relocations(file: &File<'_>) -> Vec<ComparableRelocation> {
  file
    .sections()
    .flat_map(|section| {
      let section_name = section.name().unwrap_or("unknown").to_string();
      section.relocations().map(move |(offset, relocation)| {
        let target = match relocation.target() {
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

        ComparableRelocation {
          section: section_name.clone(),
          offset,
          size: relocation.size(),
          kind: format!("{:?}", relocation.kind()),
          encoding: format!("{:?}", relocation.encoding()),
          target,
          addend: relocation.addend(),
        }
      })
    })
    .collect()
}

fn first_mismatch_offset(left: &[u8], right: &[u8]) -> Option<u64> {
  let min_len = left.len().min(right.len());
  for index in 0..min_len {
    if left[index] != right[index] {
      return Some(index as u64);
    }
  }

  if left.len() == right.len() {
    None
  } else {
    Some(min_len as u64)
  }
}
