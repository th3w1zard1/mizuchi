use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use decomp_core::{
  load_project, registered_adapters, render_report, run_request, AdapterDescriptor, ReportFormat,
  RunRequest,
};

#[derive(Debug, Parser)]
#[command(name = "decomp")]
#[command(about = "Proof-aware reverse engineering workspace orchestrator")]
struct Cli {
  #[command(subcommand)]
  command: Option<Command>,
  target: Option<PathBuf>,
  #[arg(long, default_value = "./output")]
  project: PathBuf,
  #[arg(long)]
  rebuild: bool,
  #[arg(long)]
  verify: bool,
  #[arg(long = "match")]
  match_requested: bool,
}

#[derive(Debug, Subcommand)]
enum Command {
  Adapters {
    #[arg(long, default_value = "json")]
    format: String,
  },
  Status {
    #[arg(long)]
    project: PathBuf,
  },
  Report {
    #[arg(long)]
    project: PathBuf,
    #[arg(long, default_value = "md")]
    format: String,
  },
}

fn main() {
  if let Err(err) = run() {
    eprintln!("decomp: {err:#}");
    std::process::exit(1);
  }
}

fn run() -> Result<()> {
  let cli = Cli::parse();
  let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
    .join("../..")
    .canonicalize()
    .context("failed to resolve repository root")?;

  match cli.command {
    Some(Command::Adapters { format }) => {
      let adapters = registered_adapters();
      match ReportFormat::parse(&format)? {
        ReportFormat::Json => println!("{}", serde_json::to_string_pretty(&adapters)?),
        ReportFormat::Markdown => println!("{}", render_adapters_markdown(&adapters)),
      }
    }
    Some(Command::Status { project }) => {
      let snapshot = load_project(&project)?;
      let status = snapshot.status(&project);
      println!("{}", serde_json::to_string_pretty(&status)?);
    }
    Some(Command::Report { project, format }) => {
      let format = ReportFormat::parse(&format)?;
      println!("{}", render_report(&project, format)?);
    }
    None => {
      let target = cli
        .target
        .context("target path is required unless a subcommand is used")?;
      let request = RunRequest {
        target,
        project: cli.project.clone(),
        rebuild: cli.rebuild,
        verify: cli.verify,
        match_requested: cli.match_requested,
      };
      let snapshot = run_request(&request, &repo_root)?;
      println!("project: {}", cli.project.display());
      println!("case: {}", snapshot.case.case_id);
      println!("adapter: {}", snapshot.case.adapter.id);
      println!("verification: {}", snapshot.verification.status);
      if snapshot.verification.failure_classes.is_empty() {
        println!("failures: none");
      } else {
        let failures = snapshot
          .verification
          .failure_classes
          .iter()
          .map(ToString::to_string)
          .collect::<Vec<_>>()
          .join(", ");
        println!("failures: {failures}");
      }
      if let Some(profile) = snapshot.build_plan.toolchain.recommended_profile.as_deref() {
        println!("recommended-profile: {profile}");
      }
      println!("compiler-invocation: {}", snapshot.compiler_invocation.status);
      let status = snapshot.status(&cli.project);
      if let Some(action) = status.next_actions.first() {
        println!("next-action: {} (priority={})", action.action, action.priority);
      }
      println!("report: {}", cli.project.join("report.md").display());
    }
  }

  Ok(())
}

fn render_adapters_markdown(adapters: &[AdapterDescriptor]) -> String {
  let mut out = String::from("# decomp adapters\n\n");
  out.push_str("| Adapter | Family | Platform | Load tool | Providers | Recovery |\n");
  out.push_str("|---------|--------|----------|-----------|-----------|----------|\n");
  for adapter in adapters {
    let recovery = if adapter.supports_recovery {
      "supported"
    } else {
      "probe-only"
    };
    let providers = if adapter.analysis_providers.is_empty() {
      "none".to_string()
    } else {
      adapter
        .analysis_providers
        .iter()
        .map(|provider| format!("{}:{}", provider.role, provider.id))
        .collect::<Vec<_>>()
        .join(", ")
    };
    out.push_str(&format!(
      "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |\n",
      adapter.id, adapter.family, adapter.platform, adapter.load_tool, providers, recovery
    ));
  }
  out
}
