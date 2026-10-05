use motionwright_native::build_application;
use motionwright_service::StudioService;
use semwright_native_sdk::{NativeDriver, serve};

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("motionwright native provider failed: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::var_os("MOTIONWRIGHT_DB")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::args_os().nth(1).map(std::path::PathBuf::from))
        .ok_or("MOTIONWRIGHT_DB or a database path argument is required")?;
    let service = StudioService::open(path)?;
    let app = build_application(service)?;
    serve(NativeDriver::new(app)?).await?;
    Ok(())
}
