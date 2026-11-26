//! The URI context's native owner, not headers or selector JSON, authorizes IO.
use crate::asset_preview::{self, RangeRequest};
use tauri::{
    Manager,
    http::{Request, Response, StatusCode, header},
};
pub fn handle(app: &tauri::AppHandle, label: &str, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let result = (|| {
        let owner = app
            .try_state::<crate::desktop_owner::DesktopOwner>()
            .ok_or(StatusCode::FORBIDDEN)?;
        owner
            .authorize_protocol(app, label)
            .map_err(|_| StatusCode::FORBIDDEN)?;
        let phase = owner.phase().map_err(|_| StatusCode::FORBIDDEN)?;
        if !request.body().is_empty() || request.headers().get_all(header::RANGE).iter().count() > 1
        {
            return Err(StatusCode::BAD_REQUEST);
        }
        let selected = RangeRequest {
            stream_token: asset_preview::stream_token(
                request.uri().scheme_str(),
                request.uri().authority().map(|part| part.as_str()),
                request.uri().path(),
                request.uri().query().is_some(),
            )
            .map_err(|_| StatusCode::BAD_REQUEST)?,
            method: request.method().to_string(),
            range: request
                .headers()
                .get(header::RANGE)
                .map(|value| value.to_str().map(str::to_owned))
                .transpose()
                .map_err(|_| StatusCode::BAD_REQUEST)?,
        };
        let state = app
            .try_state::<crate::AppState>()
            .ok_or(StatusCode::FORBIDDEN)?;
        let body = state
            .asset_previews
            .read(&state.service, &selected)
            .map_err(|code| StatusCode::from_u16(code).unwrap_or(StatusCode::GONE))?;
        drop(phase);
        owner
            .authorize_protocol(app, label)
            .map_err(|_| StatusCode::FORBIDDEN)?;
        let _phase = owner.phase().map_err(|_| StatusCode::FORBIDDEN)?;
        // Recheck closed-stream and current Project after the native owner phase
        // is reacquired. HEAD is bounded and reads no media payload a second time.
        if body.status != 416 {
            state
                .asset_previews
                .read(
                    &state.service,
                    &RangeRequest {
                        method: "HEAD".into(),
                        range: None,
                        ..selected
                    },
                )
                .map_err(|code| StatusCode::from_u16(code).unwrap_or(StatusCode::GONE))?;
        }
        let mut response = Response::builder()
            .status(body.status)
            .header(header::CONTENT_TYPE, body.media_type)
            .header(header::CONTENT_LENGTH, body.content_length.to_string())
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CACHE_CONTROL, "no-store")
            .header("X-Content-Type-Options", "nosniff");
        if let Some(range) = body.content_range {
            response = response.header(header::CONTENT_RANGE, range);
        }
        Ok(response
            .body(body.body)
            .expect("fixed bounded source media headers"))
    })();
    result.unwrap_or_else(crate::av_delivery::streaming::failure)
}
