//! v0.1-only conformance of a source-exact GTK/glib advisory backport.
//! This is not product code, and green tests are not evidence of a patched
//! installed Motionwright Linux binary or human product acceptance.
#[cfg(test)]
mod tests {
    use glib::{prelude::*, Variant};

    fn variant(values: &[&str]) -> Variant {
        Variant::array_from_iter::<String>(values.iter().map(|value| (*value).to_variant()))
    }

    #[test]
    fn empty_and_single_item_string_variants() {
        let empty = variant(&[]);
        let mut iter = empty.array_iter_str().expect("array of strings");
        assert_eq!(iter.next(), None);
        assert_eq!(iter.next_back(), None);
        assert_eq!(iter.len(), 0);

        let single = variant(&["one"]);
        let mut iter = single.array_iter_str().unwrap();
        assert_eq!(iter.next_back(), Some("one"));
        assert_eq!(iter.next(), None);
    }

    #[test]
    fn forward_and_reverse_utf8_iteration() {
        let source = ["alpha", "niño", "東京", "🦀", "omega"];
        let v = variant(&source);
        let forward: Vec<_> = v.array_iter_str().unwrap().collect();
        assert_eq!(forward, source);
        let backward: Vec<_> = v.array_iter_str().unwrap().rev().collect();
        assert_eq!(backward, source.into_iter().rev().collect::<Vec<_>>());
    }

    #[test]
    fn alternating_front_back_and_exact_size() {
        let v = variant(&["0", "1", "2", "3", "4", "5"]);
        let mut iter = v.array_iter_str().unwrap();
        assert_eq!(iter.size_hint(), (6, Some(6)));
        assert_eq!(iter.next(), Some("0"));
        assert_eq!(iter.next_back(), Some("5"));
        assert_eq!(iter.len(), 4);
        assert_eq!(iter.nth(1), Some("2"));
        assert_eq!(iter.nth_back(0), Some("4"));
        assert_eq!(iter.next(), Some("3"));
        assert_eq!(iter.next_back(), None);
        assert_eq!(iter.len(), 0);
    }

    #[test]
    fn all_lengths_0_through_64_are_lossless_in_both_directions() {
        let words: Vec<String> = (0..64).map(|n| format!("word-{n:02}")).collect();
        for n in 0..=64 {
            let refs: Vec<&str> = words[..n].iter().map(String::as_str).collect();
            let v = variant(&refs);
            let forward: Vec<_> = v.array_iter_str().unwrap().collect();
            let reverse: Vec<_> = v.array_iter_str().unwrap().rev().collect();
            assert_eq!(forward, refs);
            assert_eq!(reverse, refs.into_iter().rev().collect::<Vec<_>>());
        }
    }
}
