use crate::model::{
  derive_attempt_plan, derive_blocker_domains, derive_build_unit_verification_ledger,
  derive_byte_equivalence_ledger, derive_proof_target_ledger,
  derive_compiler_compatibility_ledger, derive_drift_analysis, derive_roundtrip_proof,
  derive_source_verification_ledger, derive_verification_matrix, FailureClass, ProjectSnapshot,
  ReportFormat,
};

pub fn render(snapshot: &ProjectSnapshot, format: ReportFormat) -> anyhow::Result<String> {
  match format {
    ReportFormat::Json => Ok(serde_json::to_string_pretty(snapshot)?),
    ReportFormat::Markdown => Ok(render_markdown(snapshot)),
  }
}

fn render_markdown(snapshot: &ProjectSnapshot) -> String {
  let failures = if snapshot.verification.failure_classes.is_empty() {
    "none".to_string()
  } else {
    snapshot
      .verification
      .failure_classes
      .iter()
      .map(FailureClass::to_string)
      .collect::<Vec<_>>()
      .join(", ")
  };
  let blocker_domains = {
    let derived = derive_blocker_domains(&snapshot.verification.failure_classes);
    if derived.is_empty() {
      "none".to_string()
    } else {
      derived.join(", ")
    }
  };

  let checks = snapshot
    .verification
    .checks
    .iter()
    .map(|check| format!("- `{}`: {} ({})", check.name, check.status, check.detail))
    .collect::<Vec<_>>()
    .join("\n");

  let uncertainties = snapshot
    .uncertainty
    .items
    .iter()
    .map(|item| format!("- `{}`: {}", item.failure_class, item.summary))
    .collect::<Vec<_>>()
    .join("\n");

  let artifact_comparison = snapshot
    .verification
    .artifact_comparison
    .as_ref()
    .map(|comparison| {
      format!(
        "byte_equal={}, sections_equal={}, symbols_equal={}, relocations_equal={}",
        comparison.byte_equal,
        comparison.section_inventory_equal,
        comparison.symbol_inventory_equal,
        comparison.relocation_inventory_equal,
      )
    })
    .unwrap_or_else(|| "not run".to_string());

  let build_blockers = if snapshot.build_plan.blockers.is_empty() {
    "none".to_string()
  } else {
    snapshot
      .build_plan
      .blockers
      .iter()
      .map(|blocker| format!("- {blocker}"))
      .collect::<Vec<_>>()
      .join("\n")
  };
  let compiler_profiles = snapshot
    .build_plan
    .toolchain
    .candidate_profiles
    .iter()
    .map(|profile| {
      format!(
        "- `{}`: {} ({}, {}, score={}, confidence={}, components={})",
        profile.id,
        profile.family,
        profile.vendor,
        profile.status,
        profile.evidence_score,
        profile.evidence_confidence,
        profile.required_components.len()
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let proof_targets = derive_proof_target_ledger(snapshot);
  let proof_target_rows = proof_targets
    .units
    .iter()
    .map(|unit| {
      format!(
        "- `{}`: {} (kind={}, target={}, locator={}, blockers={})",
        unit.build_unit_id,
        unit.status,
        unit.kind,
        unit.proof_target,
        unit.locator,
        unit.blockers.len()
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let build_unit_verification = derive_build_unit_verification_ledger(snapshot);
  let build_unit_verification_rows = build_unit_verification
    .units
    .iter()
    .map(|unit| {
      format!(
        "- `{}`: {} (source={}, object={}, proof_target={}, attributed={}, exact_invocation={}, blockers={})",
        unit.id,
        unit.status,
        unit.source_path,
        unit.object_path,
        unit.proof_target,
        unit.proof_attributed,
        unit.exact_invocation_recovered,
        unit.blockers.len(),
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let source_verification = derive_source_verification_ledger(snapshot);
  let source_verification_artifacts = source_verification
    .artifacts
    .iter()
    .map(|artifact| {
      format!(
        "- `{}`: {} (audit={}, proof_attributed={}, exact_invocation={}, byte_proof={}, blockers={})",
        artifact.path,
        artifact.status,
        artifact.audit_verdict,
        artifact.proof_attributed,
        artifact.exact_invocation_recovered,
        artifact.byte_equivalent_proof,
        artifact.blockers.len(),
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let translation_units = snapshot
    .reconstruction
    .project_structure
    .translation_units
    .iter()
    .map(|unit| {
      format!(
        "- `{}`: {} -> {} ({}, profiles={})",
        unit.id,
        unit.source_path,
        unit.object_path,
        unit.status,
        unit.compiler_profile_candidates.join(", ")
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let link_units = snapshot
    .reconstruction
    .project_structure
    .link_units
    .iter()
    .map(|unit| {
      format!(
        "- `{}`: {} ({}, linker_profiles={}, link_inputs={}, runtime_artifacts={})",
        unit.id,
        unit.artifact_path,
        unit.status,
        unit.linker_profile_candidates.join(", "),
        unit.link_inputs.len(),
        unit.runtime_artifacts.len()
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let function_boundaries = snapshot
    .reconstruction
    .functions
    .iter()
    .take(10)
    .map(|function| {
      format!(
        "- `{}`: size={}, source={}, confidence={}, cfg={}",
        function.name, function.size, function.source, function.confidence, function.cfg_status
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let type_candidates = snapshot
    .type_relations
    .type_candidates
    .iter()
    .take(10)
    .map(|candidate| {
      format!(
        "- `{}`: kind={}, status={}, confidence={}, symbols={}",
        candidate.id,
        candidate.kind,
        candidate.status,
        candidate.confidence,
        candidate.source_symbols.join(", ")
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let cfg_functions = snapshot
    .cfg_evidence
    .functions
    .iter()
    .take(10)
    .map(|function| {
      format!(
        "- `{}`: status={}, confidence={}, blocks={}, edges={}",
        function.name,
        function.status,
        function.confidence,
        function
          .basic_block_count
          .map(|count| count.to_string())
          .unwrap_or_else(|| "unresolved".to_string()),
        function
          .edge_count
          .map(|count| count.to_string())
          .unwrap_or_else(|| "unresolved".to_string())
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let dependency_imports = snapshot
    .dependency_graph
    .imports
    .iter()
    .take(10)
    .map(|import| {
      format!(
        "- `{}`: {}!{} ({}, confidence={})",
        import.id, import.library, import.symbol, import.status, import.confidence
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let dependency_requirements = snapshot
    .dependency_graph
    .link_requirements
    .iter()
    .take(10)
    .map(|requirement| {
      format!(
        "- `{}`: {} ({}, blockers={})",
        requirement.id,
        requirement.kind,
        requirement.status,
        requirement.blockers.len()
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let source_artifacts = snapshot
    .source_audit
    .artifacts
    .iter()
    .map(|artifact| {
      format!(
        "- `{}`: {} (marked_blocking={}, compile_blocking={})",
        artifact.path, artifact.verdict, artifact.marked_blocking, artifact.compile_blocking
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let build_units = snapshot
    .build_plan
    .build_units
    .iter()
    .map(|unit| {
      format!(
        "- `{}`: {} -> {} ({}, proof={} [{}])",
        unit.id,
        unit.source_path,
        unit.object_path,
        unit.status,
        unit.proof_target,
        unit.proof_target_status
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let compiler_invocations = snapshot
    .compiler_invocation
    .invocations
    .iter()
    .take(10)
    .map(|invocation| {
      format!(
        "- `{}`: profile={}, build_unit={}, status={}, exact_command_recovered={}, tools={}",
        invocation.id,
        invocation.profile_id,
        invocation.build_unit_id,
        invocation.status,
        invocation.exact_command_recovered,
        invocation.tool_candidates.len()
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let toolchain_stages = snapshot
    .build_plan
    .toolchain
    .stages
    .iter()
    .map(|stage| {
      format!(
        "- `{}`: {} ({}, components={})",
        stage.id,
        stage.name,
        stage.status,
        stage.required_components.join(", ")
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let link_inputs = snapshot
    .build_plan
    .link_plan
    .inputs
    .iter()
    .map(|input| format!("- `{}`: {} ({}, {})", input.name, input.kind, input.status, input.source))
    .collect::<Vec<_>>()
    .join("\n");
  let runtime_artifacts = snapshot
    .build_plan
    .link_plan
    .runtime_artifacts
    .iter()
    .map(|artifact| format!("- `{}`: {} ({})", artifact.name, artifact.kind, artifact.status))
    .collect::<Vec<_>>()
    .join("\n");
  let build_backends = snapshot
    .build_plan
    .build_system
    .candidate_backends
    .iter()
    .map(|backend| format!("{} ({})", backend.id, backend.status))
    .collect::<Vec<_>>()
    .join(", ");
  let generated_build_artifacts = snapshot
    .build_plan
    .build_system
    .generated_artifacts
    .iter()
    .map(|artifact| artifact.path.as_str())
    .collect::<Vec<_>>()
    .join(", ");
  let triple_candidates = snapshot
    .analysis
    .target
    .platform_fingerprint
    .triple_candidates
    .join(", ");
  let binary_interface_hypotheses = snapshot
    .analysis
    .target
    .platform_fingerprint
    .binary_interface_hypotheses
    .join(", ");
  let analysis_provider_summary = snapshot
    .analysis
    .tool_availability
    .analysis_providers
    .iter()
    .map(|provider| {
      format!(
        "- `{}`: {} ({}, available={}, evidence={})",
        provider.id,
        provider.role,
        provider.kind,
        provider.available,
        provider.evidence.join(", ")
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let attempt_priorities = render_attempt_priorities(snapshot);
  let verification_matrix = derive_verification_matrix(snapshot);
  let verification_matrix_rows = verification_matrix
    .rows
    .iter()
    .map(|row| {
      format!(
        "- `{}`: {} {} ({}, artifact={}, blocking={}, score={:.1}/{:.1})",
        row.name,
        row.authority,
        row.status,
        row.domain,
        row.artifact,
        row.blocking,
        row.score,
        row.weight,
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let roundtrip = derive_roundtrip_proof(snapshot);
  let roundtrip_stages = roundtrip
    .stages
    .iter()
    .map(|stage| {
      format!(
        "- `{}`: {} (authoritative={}, artifact={}, blockers={})",
        stage.name,
        stage.status,
        stage.authoritative,
        stage.artifact,
        stage.blockers.len(),
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let byte_equivalence = derive_byte_equivalence_ledger(snapshot);
  let byte_equivalence_blockers = byte_equivalence
    .blocking_rows
    .iter()
    .map(|row| {
      format!(
        "- `{}`: {} (artifact={}, failure={})",
        row.name,
        row.status,
        row.artifact,
        row
          .failure_class
          .as_ref()
          .map(ToString::to_string)
          .unwrap_or_else(|| "none".to_string())
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let compiler_compatibility = derive_compiler_compatibility_ledger(snapshot);
  let compiler_compatibility_profiles = compiler_compatibility
    .profiles
    .iter()
    .map(|profile| {
      format!(
        "- `{}`: {} ({}, source={}, exact_invocation={}, systems={})",
        profile.id,
        profile.compatibility_status,
        profile.evidence_confidence,
        profile.source_availability,
        profile.exact_invocation_status,
        profile.source_systems.join(", ")
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let drift_analysis = derive_drift_analysis(snapshot);
  let drift_items = drift_analysis
    .items
    .iter()
    .map(|item| {
      format!(
        "- `{}`: {} {} ({}, blocking={}, artifact={})",
        item.id,
        item.severity,
        item.status,
        item.category,
        item.blocking,
        item.source_artifact,
      )
    })
    .collect::<Vec<_>>()
    .join("\n");
  let score_percent = snapshot.verification.match_score.normalized * 100.0;

  let mut rendered = String::new();
  rendered.push_str(&format!(
    "# decomp report\n\n## Summary\n\n- Case: `{}`\n- Adapter: `{}`\n- Reconstruction: `{}`\n- Verification: `{}`\n- Failure classes: {}\n- Blocker domains: `{}`\n\n## Match Score\n\n- Status: `{}`\n- Score: `{:.1}/{:.1}` ({:.1}%)\n- Components: `{}`\n",
    snapshot.case.case_id,
    snapshot.case.adapter.id,
    snapshot.reconstruction.state,
    snapshot.verification.status,
    failures,
    blocker_domains,
    snapshot.verification.match_score.status,
    snapshot.verification.match_score.total_score,
    snapshot.verification.match_score.max_score,
    score_percent,
    snapshot.verification.match_score.components.len(),
  ));
  rendered.push_str(&format!(
    "\n## Verification Matrix\n\n- Ledger: `verification-matrix.json`\n- Status: `{}`\n- Authoritative proof: `{}/{}` passed\n- Advisory evidence: `{}/{}` passed\n- Policy checks: `{}/{}` passed\n- Blocked rows: `{}`\n- Coverage: `{}`\n\n{}\n",
    verification_matrix.status,
    verification_matrix.summary.authoritative_passed,
    verification_matrix.summary.authoritative_total,
    verification_matrix.summary.advisory_passed,
    verification_matrix.summary.advisory_total,
    verification_matrix.summary.policy_passed,
    verification_matrix.summary.policy_total,
    verification_matrix.summary.blocked_rows,
    verification_matrix.summary.coverage_status,
    if verification_matrix_rows.is_empty() {
      "- none".to_string()
    } else {
      verification_matrix_rows
    },
  ));
  rendered.push_str(&format!(
    "\n## Round Trip Proof\n\n- Ledger: `roundtrip.json`\n- Status: `{}`\n- Byte equivalent: `{}`\n- Proof chain complete: `{}`\n- Candidate source: `{}`\n- Candidate artifact: `{}`\n- Proof target: `{}`\n- Blockers: `{}`\n\n{}\n",
    roundtrip.status,
    roundtrip.byte_equivalent,
    roundtrip.proof_chain_complete,
    roundtrip.candidate_source,
    roundtrip.candidate_artifact,
    roundtrip.proof_target,
    roundtrip.blockers.len(),
    if roundtrip_stages.is_empty() {
      "- none".to_string()
    } else {
      roundtrip_stages
    },
  ));
  rendered.push_str(&format!(
    "\n## Byte Equivalence\n\n- Ledger: `byte-equivalence.json`\n- Status: `{}`\n- Byte equivalent: `{}`\n- Comparison available: `{}`\n- Proof target: `{}`\n- Candidate artifact: `{}`\n- First mismatch offset: `{}`\n- Blocking rows: `{}`\n\n{}\n",
    byte_equivalence.status,
    byte_equivalence.byte_equivalent,
    byte_equivalence.comparison_available,
    byte_equivalence.proof_target,
    byte_equivalence.candidate_artifact,
    byte_equivalence
      .first_mismatch_offset
      .map(|offset| offset.to_string())
      .unwrap_or_else(|| "none".to_string()),
    byte_equivalence.blocking_rows.len(),
    if byte_equivalence_blockers.is_empty() {
      "- none".to_string()
    } else {
      byte_equivalence_blockers
    },
  ));
  rendered.push_str(&format!(
    "\n## Drift Analysis\n\n- Ledger: `drift-analysis.json`\n- Status: `{}`\n- Drift items: `{}`\n- Blocking drift items: `{}`\n- Categories: `{}`\n\n{}\n",
    drift_analysis.status,
    drift_analysis.drift_count,
    drift_analysis.blocking_drift_count,
    drift_analysis.categories.len(),
    if drift_items.is_empty() {
      "- none".to_string()
    } else {
      drift_items
    },
  ));
  rendered.push_str(&format!(
    "\n## Project Structure\n\n- Source roots: `{}`\n- Include roots: `{}`\n- Build roots: `{}`\n- Artifact roots: `{}`\n- Translation units: `{}`\n- Link units: `{}`\n- Function boundaries: `{}`\n\n### Translation Units\n\n{}\n\n### Link Units\n\n{}\n\n### Function Boundaries\n\n{}\n",
    snapshot.reconstruction.project_structure.source_roots.join(", "),
    snapshot.reconstruction.project_structure.include_roots.join(", "),
    snapshot.reconstruction.project_structure.build_roots.join(", "),
    snapshot.reconstruction.project_structure.artifact_roots.join(", "),
    snapshot.reconstruction.project_structure.translation_units.len(),
    snapshot.reconstruction.project_structure.link_units.len(),
    snapshot.reconstruction.functions.len(),
    translation_units,
    link_units,
    if function_boundaries.is_empty() {
      "- none".to_string()
    } else {
      function_boundaries
    },
  ));
  rendered.push_str(&format!(
    "\n## Source Audit\n\n- Ledger: `source-audit.json`\n- Status: `{}`\n- Artifacts: `{}`\n- Marked blocking stubs: `{}`\n- Policy violations: `{}`\n\n{}\n",
    snapshot.source_audit.status,
    snapshot.source_audit.artifact_count,
    snapshot.source_audit.blocked_stub_count,
    snapshot.source_audit.suspicious_count,
    if source_artifacts.is_empty() {
      "- none".to_string()
    } else {
      source_artifacts
    },
  ));
  rendered.push_str(&format!(
    "\n## Proof Targets\n\n- Ledger: `proof-targets.json`\n- Status: `{}`\n- Collection kind: `{}`\n- Units: `{}`\n- Mapped units: `{}`\n- Unavailable units: `{}`\n- Source: `{}`\n\n{}\n",
    proof_targets.status,
    proof_targets.collection_kind,
    proof_targets.unit_count,
    proof_targets.mapped_unit_count,
    proof_targets.unavailable_unit_count,
    proof_targets.source_path,
    if proof_target_rows.is_empty() {
      "- none".to_string()
    } else {
      proof_target_rows
    },
  ));
  rendered.push_str(&format!(
    "\n## Build Unit Verification\n\n- Ledger: `build-unit-verification.json`\n- Status: `{}`\n- Units: `{}`\n- Proof-attributed units: `{}`\n- Verified units: `{}`\n- Byte-proved units: `{}`\n- Proof-unavailable units: `{}`\n\n{}\n",
    build_unit_verification.status,
    build_unit_verification.unit_count,
    build_unit_verification.proof_attributed_count,
    build_unit_verification.verified_unit_count,
    build_unit_verification.byte_proved_unit_count,
    build_unit_verification.proof_unavailable_count,
    if build_unit_verification_rows.is_empty() {
      "- none".to_string()
    } else {
      build_unit_verification_rows
    },
  ));
  rendered.push_str(&format!(
    "\n## Source Verification\n\n- Ledger: `source-verification.json`\n- Status: `{}`\n- Verified recovered source: `{}`\n- Byte-proved candidates: `{}`\n- Unverified candidates: `{}`\n- Proof-attributed artifacts: `{}`\n- Exact invocation recovered: `{}`\n\n{}\n",
    source_verification.status,
    source_verification.verified_source_count,
    source_verification.byte_proved_candidate_count,
    source_verification.unverified_source_count,
    source_verification.proof_attributed_count,
    source_verification.exact_invocation_recovered_count,
    if source_verification_artifacts.is_empty() {
      "- none".to_string()
    } else {
      source_verification_artifacts
    },
  ));
  rendered.push_str(&format!(
    "\n## Build System\n\n- Manifest kind: `{}`\n- Build graph: `build-graph.json`\n- Build manifest: `build-manifest.json`\n- Toolchain manifest: `toolchain-manifest.json`\n- Attempt matrix: `attempt-matrix.json`\n- Upstream evidence: `upstream-evidence.json`\n- Compiler compatibility: `compiler-compatibility.json`\n- Executable build emitted: `{}`\n- Preferred backend: `{}`\n- Candidate backends: `{}`\n- Generated artifacts: `{}`\n",
    snapshot.build_plan.build_system.kind,
    snapshot.build_plan.build_system.executable,
    snapshot
      .build_plan
      .build_system
      .preferred_backend
      .as_deref()
      .unwrap_or("none"),
    build_backends,
    generated_build_artifacts,
  ));
  rendered.push_str(&format!(
    "\n## Compiler Compatibility\n\n- Ledger: `compiler-compatibility.json`\n- Status: `{}`\n- Profiles: `{}`\n- Public-source modeled profiles: `{}`\n- Proprietary/unavailable gaps: `{}`\n- Exact invocation recovered: `{}`\n\n{}\n",
    compiler_compatibility.status,
    compiler_compatibility.profile_count,
    compiler_compatibility.public_source_modeled_count,
    compiler_compatibility.proprietary_gap_count,
    compiler_compatibility.exact_invocation_recovered,
    if compiler_compatibility_profiles.is_empty() {
      "- none".to_string()
    } else {
      compiler_compatibility_profiles
    },
  ));
  rendered.push_str(&format!(
    "\n## Build Plan\n\n- State: `{}`\n- Rebuild supported: `{}`\n- Source language: `{}`\n- Target format: `{}`\n- Target architecture: `{}`\n- Candidate source: `{}`\n- Candidate object: `{}`\n- Proof target: `{}`\n- Comparator: `{}`\n- Toolchain status: `{}`\n- Toolchain ranking status: `{}`\n- Recommended compiler profile: `{}`\n- Selected compiler profile: `{}`\n- Compiler: `{}`\n- Linker: `{}`\n- Toolchain stages: `{}`\n- Link plan inputs: `{}`\n- Link plan runtime artifacts: `{}`\n- Build units: `{}`\n- Dependencies: `{}`\n- Required inputs: `{}`\n\n### Attempt Priorities\n\n{}\n\n### Build Units\n\n{}\n\n### Toolchain Stages\n\n{}\n\n### Link Inputs\n\n{}\n\n### Runtime Artifacts\n\n{}\n\n### Compiler Profiles\n\n{}\n\n### Build Blockers\n\n{}\n",
    snapshot.build_plan.state,
    snapshot.build_plan.rebuild_supported,
    snapshot.build_plan.source_language,
    snapshot.build_plan.target_format,
    snapshot.build_plan.target_architecture,
    snapshot.build_plan.candidate_source,
    snapshot.build_plan.candidate_object,
    snapshot.build_plan.proof_target,
    snapshot.build_plan.expected_artifact.comparator,
    snapshot.build_plan.toolchain.status,
    snapshot.build_plan.toolchain.ranking_status,
    snapshot
      .build_plan
      .toolchain
      .recommended_profile
      .as_deref()
      .unwrap_or("none"),
    snapshot
      .build_plan
      .toolchain
      .selected_profile
      .as_deref()
      .unwrap_or("none"),
    snapshot.build_plan.toolchain.compiler,
    snapshot.build_plan.toolchain.linker,
    snapshot.build_plan.toolchain.stages.len(),
    snapshot.build_plan.link_plan.inputs.len(),
    snapshot.build_plan.link_plan.runtime_artifacts.len(),
    snapshot.build_plan.build_units.len(),
    snapshot.build_plan.dependencies.len(),
    snapshot.build_plan.required_inputs.len(),
    attempt_priorities,
    build_units,
    toolchain_stages,
    link_inputs,
    runtime_artifacts,
    compiler_profiles,
    build_blockers,
  ));
  rendered.push_str(&format!(
    "\n## Native Evidence\n\n- File kind: `{}`\n- Architecture: `{}`\n- Endianness: `{}`\n- Object format: `{}`\n- Pointer width bits: `{}`\n- Vendor: `{}`\n- Operating system: `{}`\n- Environment: `{}`\n- Triple candidates: `{}`\n- Binary interface hypotheses: `{}`\n- Entrypoint: `{:#x}`\n- Build ID: `{}`\n- Sections: `{}`\n- Segments: `{}`\n- Symbols: `{}`\n- Dynamic symbols: `{}`\n- Functions: `{}`\n- Relocations: `{}`\n- Imports: `{}`\n- Exports: `{}`\n- Debug symbols: `{}`\n- Compiler evidence: `{}`\n- Linker evidence: `{}`\n- Artifact comparison: `{}`\n",
    snapshot.analysis.target.file_kind,
    snapshot.analysis.target.architecture,
    snapshot.analysis.target.endianness,
    snapshot.analysis.target.platform_fingerprint.object_format,
    snapshot
      .analysis
      .target
      .platform_fingerprint
      .pointer_width_bits
      .map(|bits| bits.to_string())
      .unwrap_or_else(|| "unknown".to_string()),
    snapshot.analysis.target.platform_fingerprint.vendor,
    snapshot.analysis.target.platform_fingerprint.operating_system,
    snapshot.analysis.target.platform_fingerprint.environment,
    triple_candidates,
    binary_interface_hypotheses,
    snapshot.analysis.target.entry_point,
    snapshot.analysis.target.build_id.as_deref().unwrap_or("none"),
    snapshot.analysis.target.sections.len(),
    snapshot.analysis.target.segments.len(),
    snapshot.analysis.target.symbols.len(),
    snapshot.analysis.target.dynamic_symbols.len(),
    snapshot.analysis.target.functions.len(),
    snapshot.analysis.target.relocations.len(),
    snapshot.analysis.target.imports.len(),
    snapshot.analysis.target.exports.len(),
    snapshot.analysis.target.debug.has_debug_symbols,
    snapshot.analysis.target.toolchain.compiler,
    snapshot.analysis.target.toolchain.linker,
    artifact_comparison,
  ));
  rendered.push_str(&format!(
    "\n## Analysis Providers\n\n- objdiff available: `{}`\n- ghidra headless available: `{}`\n- compile placeholder available: `{}`\n\n{}\n\n## Checks\n\n{}\n\n## Uncertainty\n\n{}\n",
    snapshot.analysis.tool_availability.objdiff,
    snapshot.analysis.tool_availability.ghidra_headless,
    snapshot.analysis.tool_availability.compile_placeholder,
    analysis_provider_summary,
    checks,
    uncertainties,
  ));
  rendered.push_str(&format!(
    "\n## CFG Evidence\n\n- Ledger: `cfg-evidence.json`\n- Status: `{}`\n- Functions: `{}`\n- Recovered functions: `{}`\n- Unresolved functions: `{}`\n- Proven edges: `{}`\n- Comparison readiness: `{}`\n\n{}\n",
    snapshot.cfg_evidence.status,
    snapshot.cfg_evidence.function_count,
    snapshot.cfg_evidence.recovered_function_count,
    snapshot.cfg_evidence.unresolved_function_count,
    snapshot.cfg_evidence.edge_count,
    snapshot.cfg_evidence.comparison_readiness.status,
    if cfg_functions.is_empty() {
      "- none".to_string()
    } else {
      cfg_functions
    },
  ));
  rendered.push_str(&format!(
    "\n## Type Relations\n\n- Ledger: `type-relations.json`\n- Status: `{}`\n- Symbols: `{}`\n- Type candidates: `{}`\n- Relationships: `{}`\n- Unresolved types: `{}`\n\n{}\n",
    snapshot.type_relations.status,
    snapshot.type_relations.symbol_count,
    snapshot.type_relations.type_candidate_count,
    snapshot.type_relations.relationship_count,
    snapshot.type_relations.unresolved_type_count,
    if type_candidates.is_empty() {
      "- none".to_string()
    } else {
      type_candidates
    },
  ));
  rendered.push_str(&format!(
    "\n## Dependencies\n\n- Ledger: `dependency-graph.json`\n- Status: `{}`\n- Imports: `{}`\n- Exports: `{}`\n- Relocation edges: `{}`\n- Runtime artifacts: `{}`\n- Unresolved dependencies: `{}`\n\n### Imports\n\n{}\n\n### Link Requirements\n\n{}\n",
    snapshot.dependency_graph.status,
    snapshot.dependency_graph.import_count,
    snapshot.dependency_graph.export_count,
    snapshot.dependency_graph.relocation_edge_count,
    snapshot.dependency_graph.runtime_artifact_count,
    snapshot.dependency_graph.unresolved_dependency_count,
    if dependency_imports.is_empty() {
      "- none".to_string()
    } else {
      dependency_imports
    },
    if dependency_requirements.is_empty() {
      "- none".to_string()
    } else {
      dependency_requirements
    },
  ));
  rendered.push_str(&format!(
    "\n## Compiler Invocation\n\n- Ledger: `compiler-invocation.json`\n- Status: `{}`\n- Candidates: `{}`\n- Recovered exact invocations: `{}`\n\n{}\n",
    snapshot.compiler_invocation.status,
    snapshot.compiler_invocation.candidate_count,
    snapshot.compiler_invocation.recovered_invocation_count,
    if compiler_invocations.is_empty() {
      "- none".to_string()
    } else {
      compiler_invocations
    },
  ));
  rendered
}

fn render_attempt_priorities(snapshot: &ProjectSnapshot) -> String {
  let attempts = derive_attempt_plan(snapshot).top_attempts;
  if attempts.is_empty() {
    return "- none".to_string();
  }

  attempts
    .into_iter()
    .take(3)
    .map(|attempt| {
      format!(
        "- `{}`: status={}, priority={}, class={}, next={}",
        attempt.id, attempt.row_status, attempt.priority, attempt.priority_class, attempt.next_action
      )
    })
    .collect::<Vec<_>>()
    .join("\n")
}
