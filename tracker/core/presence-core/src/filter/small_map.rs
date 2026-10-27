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

    pub fn get_mut(&mut self, key: &K) -> Option<&mut V> {
        self.entries.iter_mut().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    pub fn contains_key(&self, key: &K) -> bool {
        self.entries.iter().any(|(k, _)| k == key)
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

    pub fn keys(&self) -> impl Iterator<Item = &K> {
        self.entries.iter().map(|(k, _)| k)
    }

    pub fn values(&self) -> impl Iterator<Item = &V> {
        self.entries.iter().map(|(_, v)| v)
    }

    pub fn values_mut(&mut self) -> impl Iterator<Item = &mut V> {
        self.entries.iter_mut().map(|(_, v)| v)
    }

    pub fn into_values(self) -> impl Iterator<Item = V> {
        self.entries.into_iter().map(|(_, v)| v)
    }

    /// Keeps the entries for which f is true, in their order.
    pub fn retain(&mut self, mut f: impl FnMut(&K, &V) -> bool) {
        self.entries.retain(|(k, v)| f(k, v));
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
