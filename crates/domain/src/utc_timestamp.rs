//! UTC timestamps with the same persisted RFC 3339 representation as existing projects.
use serde::{Deserialize, Serialize};
use std::fmt::Write;
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

const NANOS: i64 = 1_000_000_000;
const DAY: i64 = 86_400;

// Older projects can contain signed years and explicit leap seconds. Keep the
// original normalized representation and ordering instead of rounding to Unix
// seconds or changing the exported backup digest.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct UtcTimestamp {
    unix_seconds: i64,
    nanos: u32,
}

impl UtcTimestamp {
    /// Capture the current UTC wall clock at the platform precision.
    pub fn now() -> Self {
        let now = OffsetDateTime::now_utc();
        Self {
            unix_seconds: now.unix_timestamp(),
            nanos: now.nanosecond(),
        }
    }

    /// Read the existing normalized or legacy relaxed RFC 3339 form.
    pub fn parse(value: &str) -> Option<Self> {
        let mut value = value.trim();
        let signed = value.starts_with(['+', '-']);
        let negative = value.starts_with('-');
        if signed {
            value = &value[1..];
        }
        let (year, value) = digits(value, if signed { usize::MAX } else { 4 })?;
        let year = i32::try_from(year).ok()? * if negative { -1 } else { 1 };
        if !(-262_143..=262_142).contains(&year) {
            return None;
        }
        let (month, value) = digits(separator(value, '-')?, 2)?;
        let (day, value) = digits(separator(value, '-')?, 2)?;
        let value = value.strip_prefix(['T', 't', ' '])?;
        let (hour, value) = digits(value, 2)?;
        let (minute, value) = digits(separator(value, ':')?, 2)?;
        let (second, value) = digits(separator(value, ':')?, 2)?;
        if second > 60 {
            return None;
        }
        let (fraction, zone) = fraction(value)?;
        let offset = parse_zone(zone.trim())?;
        let date = Date::from_calendar_date(
            year,
            Month::try_from(u8::try_from(month).ok()?).ok()?,
            u8::try_from(day).ok()?,
        )
        .ok()?;
        let time = Time::from_hms(hour as u8, minute as u8, second.min(59) as u8).ok()?;
        let utc = PrimitiveDateTime::new(date, time).assume_offset(offset);
        // The preceding parser admitted the same calendar range as the old
        // persistent format. Check again after offset normalization.
        if !(-262_143..=262_142).contains(&utc.to_offset(UtcOffset::UTC).year()) {
            return None;
        }
        Some(Self {
            unix_seconds: utc.unix_timestamp(),
            nanos: fraction + if second == 60 { NANOS as u32 } else { 0 },
        })
    }

    /// Format the SQLite and receipt form, using an explicit UTC offset.
    pub fn to_rfc3339(self) -> String {
        format!("{}+00:00", self.format_body(false))
    }

    pub(crate) fn canonical_utc(self) -> String {
        format!("{}Z", self.format_body(true))
    }

    /// Compare whole-second authority windows and retain the handling of
    /// explicitly represented leap seconds used by previous declarations.
    pub(crate) fn seconds_since(self, earlier: Self) -> i64 {
        let a = self.unix_seconds.rem_euclid(DAY);
        let b = earlier.unix_seconds.rem_euclid(DAY);
        let leap_adjustment = i64::from(a > b && i64::from(earlier.nanos) >= NANOS)
            - i64::from(a < b && i64::from(self.nanos) >= NANOS);
        self.unix_seconds - earlier.unix_seconds
            + (i64::from(self.nanos) - i64::from(earlier.nanos)).div_euclid(NANOS)
            + leap_adjustment
    }

    /// Shift by complete UTC calendar days while retaining subsecond precision.
    pub fn checked_add_days(self, days: i64) -> Option<Self> {
        let unix_seconds = self.unix_seconds.checked_add(days.checked_mul(DAY)?)?;
        let year = OffsetDateTime::from_unix_timestamp(unix_seconds)
            .ok()?
            .year();
        (-262_143..=262_142).contains(&year).then_some(Self {
            unix_seconds,
            nanos: self.nanos,
        })
    }

    fn format_body(self, whole_seconds: bool) -> String {
        let utc = OffsetDateTime::from_unix_timestamp(self.unix_seconds)
            .expect("UTC timestamp is constructed only from supported calendar dates");
        let year = utc.year();
        let year = if (0..=9999).contains(&year) {
            format!("{year:04}")
        } else {
            format!("{year:+05}")
        };
        let leap = u8::from(i64::from(self.nanos) >= NANOS);
        let mut out = format!(
            "{year}-{:02}-{:02}T{:02}:{:02}:{:02}",
            u8::from(utc.month()),
            utc.day(),
            utc.hour(),
            utc.minute(),
            utc.second() + leap
        );
        let fraction = self.nanos % NANOS as u32;
        if !whole_seconds && fraction != 0 {
            let (digits, fraction) = if fraction.is_multiple_of(1_000_000) {
                (3, fraction / 1_000_000)
            } else if fraction.is_multiple_of(1_000) {
                (6, fraction / 1_000)
            } else {
                (9, fraction)
            };
            // Formatting into String cannot fail.
            let _ = write!(out, ".{fraction:0digits$}");
        }
        out
    }
}

fn separator(value: &str, separator: char) -> Option<&str> {
    value.trim_start().strip_prefix(separator)
}

fn digits(value: &str, width: usize) -> Option<(u32, &str)> {
    let value = value.trim_start();
    let count = value
        .bytes()
        .take(width)
        .take_while(u8::is_ascii_digit)
        .count();
    if count == 0 {
        return None;
    }
    Some((value[..count].parse().ok()?, &value[count..]))
}

fn fraction(value: &str) -> Option<(u32, &str)> {
    let Some(fraction) = value.strip_prefix('.') else {
        return Some((0, value));
    };
    let count = fraction.bytes().take_while(u8::is_ascii_digit).count();
    if count == 0 {
        return None;
    }
    let retained = count.min(9);
    let nanos: u32 = fraction[..retained].parse().ok()?;
    Some((nanos * 10_u32.pow(9 - retained as u32), &fraction[count..]))
}

fn parse_zone(zone: &str) -> Option<UtcOffset> {
    if zone.eq_ignore_ascii_case("Z") || zone.eq_ignore_ascii_case("UTC") {
        return Some(UtcOffset::UTC);
    }
    let prefix = zone.chars().next()?;
    let sign = match prefix {
        '+' => 1,
        '-' | '\u{2212}' => -1,
        _ => return None,
    };
    let zone = &zone[prefix.len_utf8()..];
    if zone.len() < 4 || !zone.as_bytes()[..2].iter().all(u8::is_ascii_digit) {
        return None;
    }
    let remainder = zone[2..].trim_start_matches(|c: char| c == ':' || c.is_whitespace());
    if remainder.len() != 2 || !remainder.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let hour = zone[..2].parse::<i8>().ok()?;
    let minute = remainder.parse::<i8>().ok()?;
    if hour > 23 || minute > 59 {
        return None;
    }
    UtcOffset::from_hms(sign * hour, sign * minute, 0).ok()
}

impl Serialize for UtcTimestamp {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&format!("{}Z", self.format_body(false)))
    }
}

impl<'de> Deserialize<'de> for UtcTimestamp {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::parse(&value).ok_or_else(|| serde::de::Error::custom("invalid project UTC timestamp"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_project_dates_keep_exact_serialized_precision_and_utc() {
        for (old, normalized) in [
            ("2026-10-17T06:01:56Z", "2026-10-17T06:01:56Z"),
            ("2026-10-17T06:01:56.120Z", "2026-10-17T06:01:56.120Z"),
            ("2026-10-17T06:01:56.336870Z", "2026-10-17T06:01:56.336870Z"),
            (
                "2026-10-16T23:01:56.336873219-07:00",
                "2026-10-17T06:01:56.336873219Z",
            ),
            ("2026- 1- 7T6: 1:56 UTC", "2026-01-07T06:01:56Z"),
            ("+10000-01-01T00:00:00Z", "+10000-01-01T00:00:00Z"),
            ("2016-12-31T23:59:60.500Z", "2016-12-31T23:59:60.500Z"),
            ("2026-10-16T23:01:56\u{2212}07:00", "2026-10-17T06:01:56Z"),
            ("2026-10-16T23:01:56-07 : : 00", "2026-10-17T06:01:56Z"),
        ] {
            let timestamp: UtcTimestamp = serde_json::from_str(&format!("\"{old}\"")).unwrap();
            assert_eq!(
                serde_json::to_string(&timestamp).unwrap(),
                format!("\"{normalized}\"")
            );
        }
        assert!(UtcTimestamp::parse("2026-02-30T00:00:00Z").is_none());
        assert!(UtcTimestamp::parse("2026-11-26T00:00:00+24:00").is_none());
    }
}
