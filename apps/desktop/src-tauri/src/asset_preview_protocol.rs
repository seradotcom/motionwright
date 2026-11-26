//! The URI context's native owner, not headers or selector JSON, authorizes IO.
use crate::asset_preview::{self, RangeRequest};
use tauri::{
    Manager,
    http::{Request, Response, StatusCode, Uri, header},
};
use uuid::Uuid;

fn token(uri: &Uri) -> Result<Uuid, StatusCode> {
    let authority = uri.authority().map(|part| part.as_str());
    if !((uri.scheme_str() == Some(asset_preview::SCHEME) && authority == Some("localhost"))
        || (uri.scheme_str() == Some("http") && authority == Some("motionwright-asset.localhost")))
        || uri.query().is_some()
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let value = uri
        .path()
        .strip_prefix('/')
        .ok_or(StatusCode::BAD_REQUEST)?;
    let token = Uuid::parse_str(value).map_err(|_| StatusCode::BAD_REQUEST)?;
    if token.to_string() != value {
        return Err(StatusCode::BAD_REQUEST);
    }
    Ok(token)
}

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
            stream_token: token(request.uri())?,
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn original_asset_protocol_rejects_remote_hosts_paths_queries_and_native_master_tokens() {
        let id = Uuid::new_v4();
        for url in [
            format!("motionwright-asset://localhost/{id}"),
            format!("http://motionwright-asset.localhost/{id}"),
        ] {
            assert_eq!(token(&url.parse().unwrap()), Ok(id));
        }
        for url in [
            format!("https://motionwright-asset.localhost/{id}"),
            format!("http://motionwright-asset.localhost:80/{id}"),
            format!("http://motionwright-asset.localhost.evil/{id}"),
            format!("motionwright-media://localhost/{id}"),
            format!("motionwright-asset://localhost/{id}?path=private"),
            "motionwright-asset://localhost/../private".into(),
        ] {
            assert!(token(&url.parse().unwrap()).is_err());
        }
    }
}
