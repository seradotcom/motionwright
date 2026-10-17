use motionwright_native_readback_driver::NativeReadbackDriver;

#[tokio::main]
async fn main() {
    // Stdout is reserved for the existing Driver SDK protocol. Do not print
    // runtime paths, decoder stderr or a fabricated result on startup failure.
    if semwright_driver_sdk::serve(NativeReadbackDriver)
        .await
        .is_err()
    {
        std::process::exit(1);
    }
}
