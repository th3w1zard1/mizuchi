use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use object::{Architecture, FileKind};

use crate::model::{AdapterDescriptor, AnalysisProvider, FailureClass, TargetInput, UncertaintyItem};

pub trait TargetAdapter {
  fn descriptor(&self) -> AdapterDescriptor;

  fn matches_extension(&self, extension: &str) -> bool {
    let _ = extension;
    false
  }

  fn matches_file_kind(&self, kind: FileKind, target: &TargetInput) -> bool {
    let _ = kind;
    let _ = target;
    false
  }
}

struct StaticAdapter {
  descriptor: AdapterDescriptor,
  extensions: &'static [&'static str],
  kinds: &'static [FileKind],
}

impl TargetAdapter for StaticAdapter {
  fn descriptor(&self) -> AdapterDescriptor {
    self.descriptor.clone()
  }

  fn matches_extension(&self, extension: &str) -> bool {
    self.extensions.iter().any(|item| *item == extension)
  }

  fn matches_file_kind(&self, kind: FileKind, _target: &TargetInput) -> bool {
    self.kinds.contains(&kind)
  }
}

pub fn probe_target(path: &Path) -> Result<(TargetInput, AdapterDescriptor, Vec<UncertaintyItem>)> {
  let bytes = fs::read(path).with_context(|| format!("failed to read target {}", path.display()))?;
  let target = TargetInput::new(path.to_path_buf(), bytes);
  let adapter = classify_target(&target);
  let uncertainties = adapter_uncertainties(&target, &adapter);
  Ok((target, adapter, uncertainties))
}

pub fn registered_adapters() -> Vec<AdapterDescriptor> {
  adapter_registry()
    .into_iter()
    .map(|adapter| adapter.descriptor())
    .collect()
}

pub fn registered_adapter(id: &str) -> Option<AdapterDescriptor> {
  adapter_registry()
    .into_iter()
    .map(|adapter| adapter.descriptor())
    .find(|adapter| adapter.id == id)
}

fn classify_target(target: &TargetInput) -> AdapterDescriptor {
  let extension = target
    .path
    .extension()
    .and_then(|ext| ext.to_str())
    .unwrap_or_default()
    .to_ascii_lowercase();

  let file_kind = FileKind::parse(target.bytes.as_slice()).ok();

  if is_ps2_elf(file_kind, target) {
    return descriptor(
      "elf-ps2",
      "ghidra-ps2-v1",
      "binary",
      "elf-ps2",
      "ps2",
      "ghidra",
      &[
        AnalysisProvider::new("ghidra", "surface", "Direct Ghidra loader and scripted analysis surface."),
      ],
      "context/ctx.h",
      true,
    );
  }

  for adapter in adapter_registry() {
    if adapter.matches_extension(&extension)
      || file_kind
        .map(|kind| adapter.matches_file_kind(kind, target))
        .unwrap_or(false)
    {
      return adapter.descriptor();
    }
  }

  descriptor(
    "unsupported",
    "unsupported-v1",
    "binary",
    "unsupported",
    "unknown",
    "unknown",
    &[],
    "unavailable",
    false,
  )
}

fn adapter_registry() -> Vec<StaticAdapter> {
  vec![
    StaticAdapter {
      descriptor: descriptor(
        "odyssey",
        "ghidra-mizuchi-v1",
        "binary",
        "odyssey",
        "xbox",
        "agdec-http",
        &[
          AnalysisProvider::new("agdec-http", "surface", "AgentDecompile HTTP bridge surface used by the current Odyssey workflow."),
          AnalysisProvider::new("ghidra", "analyzer", "Ghidra project import, analysis, and scripting backend behind the bridge."),
        ],
        "context/ctx.h",
        true,
      ),
      extensions: &["xbe"],
      kinds: &[],
    },
    StaticAdapter {
      descriptor: descriptor(
        "elf-ps2",
        "ghidra-ps2-v1",
        "binary",
        "elf-ps2",
        "ps2",
        "ghidra",
        &[
          AnalysisProvider::new("ghidra", "surface", "Direct Ghidra loader and scripted analysis surface."),
        ],
        "context/ctx.h",
        true,
      ),
      extensions: &[],
      kinds: &[],
    },
    StaticAdapter {
      descriptor: descriptor(
        "elf",
        "native-elf-v1",
        "binary",
        "elf",
        "unix",
        "native-object",
        &[
          AnalysisProvider::new("native-object", "parser", "Rust object readers provide current section, symbol, relocation, and debug inventories."),
        ],
        "unavailable",
        true,
      ),
      extensions: &[],
      kinds: &[FileKind::Elf32, FileKind::Elf64],
    },
    StaticAdapter {
      descriptor: descriptor(
        "pe",
        "native-pe-v1",
        "binary",
        "pe",
        "windows",
        "native-object",
        &[
          AnalysisProvider::new("native-object", "parser", "Rust object readers provide current section, symbol, relocation, and debug inventories."),
        ],
        "unavailable",
        true,
      ),
      extensions: &[],
      kinds: &[FileKind::Coff, FileKind::Pe32, FileKind::Pe64],
    },
    StaticAdapter {
      descriptor: descriptor(
        "macho",
        "native-macho-v1",
        "binary",
        "macho",
        "macos",
        "native-object",
        &[
          AnalysisProvider::new("native-object", "parser", "Rust object readers provide current section, symbol, relocation, and debug inventories."),
        ],
        "unavailable",
        true,
      ),
      extensions: &[],
      kinds: &[
        FileKind::MachO32,
        FileKind::MachO64,
        FileKind::MachOFat32,
        FileKind::MachOFat64,
      ],
    },
    StaticAdapter {
      descriptor: descriptor(
        "static-lib",
        "native-archive-v1",
        "archive",
        "static-lib",
        "multi",
        "native-object",
        &[
          AnalysisProvider::new("native-object", "parser", "Rust object readers provide current archive/member probe metadata."),
        ],
        "unavailable",
        true,
      ),
      extensions: &["a", "lib"],
      kinds: &[FileKind::Archive],
    },
    StaticAdapter {
      descriptor: descriptor(
        "firmware-blob",
        "raw-firmware-v1",
        "firmware-blob",
        "firmware-blob",
        "unknown",
        "raw-inspector",
        &[
          AnalysisProvider::new("raw-inspector", "probe", "Raw byte inspection without a structured object-format parser."),
        ],
        "unavailable",
        false,
      ),
      extensions: &["bin", "rom", "fw", "img"],
      kinds: &[],
    },
  ]
}

fn is_ps2_elf(file_kind: Option<FileKind>, target: &TargetInput) -> bool {
  matches!(file_kind, Some(FileKind::Elf32 | FileKind::Elf64))
    && (target
      .parsed_architecture()
      .map(|arch| matches!(arch, Architecture::Mips | Architecture::Mips64))
      .unwrap_or(false)
      || target
        .path
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| name.to_ascii_lowercase().contains("ps2"))
        .unwrap_or(false))
}

fn descriptor(
  id: &str,
  capabilities_profile: &str,
  source_type: &str,
  family: &str,
  platform: &str,
  load_tool: &str,
  analysis_providers: &[AnalysisProvider],
  context_path: &str,
  supports_recovery: bool,
) -> AdapterDescriptor {
  AdapterDescriptor::new(
    id,
    capabilities_profile,
    source_type,
    family,
    platform,
    load_tool,
    analysis_providers,
    context_path,
    supports_recovery,
  )
}

fn adapter_uncertainties(target: &TargetInput, adapter: &AdapterDescriptor) -> Vec<UncertaintyItem> {
  let mut items = Vec::new();

  if !adapter.supports_recovery {
    items.push(UncertaintyItem::blocking(
      "adapter-probe-only",
      FailureClass::UnsupportedFormat,
      format!(
        "Adapter '{}' is currently probe-only in the Rust orchestrator slice.",
        adapter.id
      ),
      vec![
        format!("input={}", target.path.display()),
        format!("adapter={}", adapter.id),
      ],
    ));
  }

  items
}
