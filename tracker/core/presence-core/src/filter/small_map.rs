//! A small map in the order of insertion, as Python's dict: the order in which people are summed and
//! weighed one after the other stays that of the Python filter (docs/rust-core.md 2.3). No HashMap in
//! paths that compute: its order of iteration differs between processes.

#[derive(Debug, Clone, PartialEq)]
pub struct SmallMap<K, V> {
    entries: Vec<(K, V)>,
}

impl<K, V> Default for SmallMap<K, V> {
    fn default() -> Self {
        Self { entries: Vec::new() }
    }
}

impl<K: PartialEq, V> SmallMap<K, V> {
    pub fn get(&self, key: &K) -> Option<&V> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// Replaces the value of a key in place, or appends the key.
    pub fn insert(&mut self, key: K, value: V) {
        match self.entries.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => *v = value,
            None => self.entries.push((key, value)),
        }
    }

    pub fn remove(&mut self, key: &K) -> Option<V> {
        let at = self.entries.iter().position(|(k, _)| k == key)?;
        Some(self.entries.remove(at).1)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&K, &V)> {
        self.entries.iter().map(|(k, v)| (k, v))
    }

    pub fn values(&self) -> impl Iterator<Item = &V> {
        self.entries.iter().map(|(_, v)| v)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

impl<K: PartialEq, V> FromIterator<(K, V)> for SmallMap<K, V> {
    fn from_iter<I: IntoIterator<Item = (K, V)>>(iter: I) -> Self {
        let mut map = SmallMap::default();
        for (k, v) in iter {
            map.insert(k, v);
        }
        map
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_order_of_insertion() {
        let mut m: SmallMap<u32, &str> = [(3, "c"), (1, "a")].into_iter().collect();
        m.insert(2, "b");
        m.insert(3, "C");
        assert_eq!(
            m.iter().map(|(k, v)| (*k, *v)).collect::<Vec<_>>(),
            vec![(3, "C"), (1, "a"), (2, "b")]
        );
        assert_eq!(m.remove(&1), Some("a"));
        assert_eq!(m.get(&2), Some(&"b"));
        assert_eq!(m.len(), 2);
    }
}
