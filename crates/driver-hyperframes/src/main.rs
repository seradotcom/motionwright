use motionwright_driver_hyperframes::driver::HyperframesDriver;
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let result = match HyperframesDriver::production() {
        Ok(driver) => semwright_driver_sdk::serve(driver).await,
        Err(error) => Err(error),
    };
    if let Err(error) = result {
        eprintln!("hyperframes-driver=NOT_VERIFIED");
        std::process::exit(error.exit_code());
    }
}
