import { StyleSheet, Text, View } from 'react-native';

export function ScreenHeader({ title, subtitle }) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>{title}</Text>
      {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 4,
    paddingBottom: 16,
  },
  title: {
    color: '#0f172a',
    fontSize: 24,
    fontWeight: '800',
  },
  subtitle: {
    color: '#475569',
    fontSize: 14,
    lineHeight: 20,
  },
});
