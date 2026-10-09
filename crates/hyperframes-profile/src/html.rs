use crate::*;
use sha2::{Digest, Sha256};
/// The retained source is a native HTML composition using HyperFrames + GSAP,
/// not a Film translation. Runtime script bytes are separately owner-pinned.
pub fn compile_html(doc: &HyperframesDocument) -> Result<String> {
    validate_document(doc)?;
    let json = serde_json::to_string(doc)
        .map_err(|e| ProfileError(e.to_string()))?
        .replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('\u{2028}', "\\u2028")
        .replace('\u{2029}', "\\u2029");
    let c = &doc.canvas;
    let background = c.background.as_deref().unwrap_or("transparent");
    let mut fonts = String::new();
    for node in &doc.nodes {
        if let Content::Text {
            font: Font::Asset { asset_id, family },
            ..
        } = &node.content
        {
            let asset = doc
                .assets
                .iter()
                .find(|a| a.id == *asset_id)
                .expect("validated asset");
            // Family is restricted to an inert ASCII label by validate_document.
            fonts.push_str(&format!("@font-face{{font-family:'{family}';src:url('/assets/{}.woff2') format('woff2');font-weight:100 900;font-display:block;}}",asset.sha256));
        }
    }
    Ok(format!(
        r#"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self'; media-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<title>Motionwright native HTML composition</title>
<style>@font-face{{font-family:'Motionwright Sans';src:url('/runtime/sans.woff2') format('woff2');font-weight:400 700;font-display:block;}}@font-face{{font-family:'Motionwright Mono';src:url('/runtime/mono.woff2') format('woff2');font-weight:400;font-display:block;}}{fonts}
*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:{background};}}#main{{position:relative;width:{width}px;height:{height}px;overflow:hidden;background:{background};isolation:isolate;}}#mw-world{{position:absolute;left:0;top:0;width:{width}px;height:{height}px;transform-origin:center center;}}.mw-node{{position:absolute;left:0;top:0;margin:0;transform-origin:center center;}}.mw-text{{white-space:pre-wrap;font-synthesis:none;overflow:visible;}}.mw-node img,.mw-node video,.mw-node svg{{display:block;width:100%;height:100%;}}
</style><script src="/runtime/gsap.js"></script><script src="/runtime/hyperframes.js"></script></head>
<body><main id="main" data-composition-id="main" data-width="{width}" data-height="{height}" data-fps="{fps}"><div id="mw-world"></div></main>
<script id="mw-source" type="application/json">{json}</script><script src="/runtime/authoring.js"></script></body></html>
"#,
        width = c.width,
        height = c.height,
        fps = f64::from(c.rate.num) / f64::from(c.rate.den)
    ))
}
pub fn source_digest(doc: &HyperframesDocument) -> Result<String> {
    Ok(hex::encode(Sha256::digest(compile_html(doc)?.as_bytes())))
}
