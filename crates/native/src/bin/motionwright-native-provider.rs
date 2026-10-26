use motionwright_native::run_native_provider;

#[tokio::main]
async fn main() {
    if let Err(error) = run_native_provider().await {
        eprintln!("motionwright native provider failed: {error}");
        std::process::exit(1);
    }
}
