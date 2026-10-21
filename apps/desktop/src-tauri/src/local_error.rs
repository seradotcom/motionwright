//! Shared desktop error presentation; behavior retained during export extraction.

pub(crate) fn sanitized(error: impl std::fmt::Display) -> String {
    let text = error.to_string();
    if text.contains("stale project base")
        || text.contains("generation differs")
        || text.contains("request id was reused")
    {
        return text;
    }
    if text.contains("locked")
        || text.contains("invalid project")
        || text.contains("not found")
        || text.contains("project already exists")
        || text.contains("invalid project backup")
        || text.contains("invalid blob digest")
        || text.contains("blob ")
        || text.contains("export destination already exists")
        || text.contains("unsafe source path")
        || text.contains("storage schema")
    {
        return text;
    }
    "Motionwright could not complete the local operation".into()
}
