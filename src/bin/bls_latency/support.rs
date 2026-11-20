use serde_json::{json, Value};
use std::fs;
use std::hint::black_box;
use std::io;
use std::process::Command;
use std::time::Instant;

pub const WARMUPS: usize = 50;
pub const SAMPLES: usize = 1000;

pub fn time<T>(run: impl FnOnce() -> T) -> (T, u128) {
    let start = Instant::now();
    let result = black_box(run());
    (result, start.elapsed().as_nanos())
}

pub fn percentile(sorted: &[u128], percent: usize) -> u128 {
    sorted[(sorted.len() * percent).div_ceil(100) - 1]
}

pub fn statistics_us(times: &mut [u128]) -> (f64, f64, f64) {
    times.sort_unstable();
    let mean = times.iter().sum::<u128>() as f64 / times.len() as f64;
    (
        mean / 1000.0,
        percentile(times, 95) as f64 / 1000.0,
        percentile(times, 99) as f64 / 1000.0,
    )
}

fn proc_field<'a>(file: &'a str, field: &str) -> Option<&'a str> {
    file.lines()
        .filter_map(|line| line.split_once(':'))
        .find_map(|(name, value)| (name.trim() == field).then(|| value.trim()))
}

pub fn environment() -> io::Result<Value> {
    let rustc = Command::new("rustc").arg("--version").output()?;
    if !rustc.status.success() {
        return Err(io::Error::other("rustc --version failed"));
    }
    let version = String::from_utf8(rustc.stdout).map_err(io::Error::other)?;
    let cpu = fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    let host_logical_cpus = cpu
        .lines()
        .filter(|line| {
            line.split_once(':')
                .is_some_and(|(k, _)| k.trim() == "processor")
        })
        .count();
    Ok(json!({
        "cpu_model": proc_field(&cpu, "model name"),
        "host_logical_cpus": host_logical_cpus,
        "process_cpu_affinity": proc_field(&status, "Cpus_allowed_list"),
        "process_available_parallelism": std::thread::available_parallelism()?.get(),
        "benchmark_driver_threads": 1,
        "rustc": version.trim(),
        "build_mode": "release",
        "arch": std::env::consts::ARCH,
        "os": std::env::consts::OS,
        "warmups_per_operation": WARMUPS,
        "samples_per_operation": SAMPLES,
        "percentile_method": "nearest_rank",
    }))
}
