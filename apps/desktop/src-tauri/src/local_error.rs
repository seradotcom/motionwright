//! Shared desktop error presentation without dynamic backend details.

pub(crate) fn sanitized(error: impl std::fmt::Display) -> String {
    let text = error.to_string();
    // Parser diagnostics can quote arbitrary source content, including words
    // that resemble an error category. Never forward that diagnostic to IPC.
    if text.starts_with("serialization:") {
        return "invalid project data".into();
    }
    for (category, message) in [
        ("stale project base", "stale project base"),
        (
            "generation differs",
            "resource generation differs from expected base",
        ),
        (
            "request id was reused",
            "request id was reused with a different payload",
        ),
        ("locked", "project is locked"),
        ("invalid project backup", "invalid project backup"),
        ("invalid project", "invalid project"),
        ("not found", "project or resource not found"),
        ("project already exists", "project already exists"),
        ("invalid blob digest", "invalid blob digest"),
        ("blob ", "blob validation failed"),
        (
            "export destination already exists",
            "export destination already exists",
        ),
        ("unsafe source path", "unsafe source path"),
        ("storage schema", "storage schema is not supported"),
    ] {
        if text.contains(category) {
            return message.into();
        }
    }
    "Motionwright could not complete the local operation".into()
}

#[cfg(test)]
mod tests {
    use super::sanitized;

    #[test]
    fn recognized_categories_never_echo_surrounding_backend_details() {
        for category in [
            "stale project base",
            "generation differs",
            "request id was reused",
            "locked",
            "invalid project",
            "not found",
            "project already exists",
            "invalid project backup",
            "invalid blob digest",
            "blob ",
            "export destination already exists",
            "unsafe source path",
            "storage schema",
        ] {
            let diagnostic = format!("MW_PRIVATE_PREFIX {category}: MW_PRIVATE_SUFFIX");
            let presented = sanitized(&diagnostic);
            assert!(!presented.contains("MW_PRIVATE_PREFIX"));
            assert!(!presented.contains("MW_PRIVATE_SUFFIX"));
            assert_ne!(presented, diagnostic);
        }
    }

    #[test]
    fn parser_content_cannot_select_a_passthrough_error() {
        let diagnostic =
            "serialization: invalid type: string \"MW_PRIVATE_METADATA_SENTINEL not found\"";
        assert_eq!(sanitized(diagnostic), "invalid project data");
        assert_eq!(
            sanitized("unrecognized backend detail MW_PRIVATE_METADATA_SENTINEL"),
            "Motionwright could not complete the local operation"
        );
    }

    #[test]
    fn conflict_and_reused_request_categories_remain_distinct() {
        assert_eq!(
            sanitized("stale project base: expected 3, actual 4"),
            "stale project base"
        );
        assert_eq!(
            sanitized("resource generation differs from expected base"),
            "resource generation differs from expected base"
        );
        assert_eq!(
            sanitized("request id was reused with a different payload"),
            "request id was reused with a different payload"
        );
        assert_eq!(
            sanitized("export destination already exists"),
            "export destination already exists"
        );
    }
}
