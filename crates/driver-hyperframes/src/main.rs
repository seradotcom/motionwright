#[tokio::main]
async fn main() -> semwright_types::Result<()> {
    semwright_driver_sdk::serve(motionwright_hyperframes_driver::HyperframesDriver).await
}
