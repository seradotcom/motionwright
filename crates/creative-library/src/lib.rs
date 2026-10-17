//! Bounded observation of an already attached native source.
//!
//! This small library preserves the original bytes and distinguishes visible
//! structure from opaque data. It has no renderer, imported-code executor,
//! project mutation, native application grant or semantic editing authority.

mod attached;
pub use attached::*;

use thiserror::Error;

#[derive(Debug, Clone, Error, PartialEq, Eq)]
#[error("{0}")]
pub struct CraftError(pub String);

pub type Result<T> = std::result::Result<T, CraftError>;

pub(crate) fn check(condition: bool, reason: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(CraftError(reason.into()))
    }
}
