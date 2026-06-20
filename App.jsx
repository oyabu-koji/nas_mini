import { StatusBar } from 'expo-status-bar';
import { StyleSheet, Text, View } from 'react-native';

export default function App() {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Expo project baseline</Text>
      <Text style={styles.body}>React Native + Expo SDK 54 + JavaScript</Text>
      <StatusBar style="auto" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    backgroundColor: '#f7f7f8',
  },
  title: {
    color: '#1f2937',
    fontSize: 24,
    fontWeight: '700',
    textAlign: 'center',
  },
  body: {
    marginTop: 12,
    color: '#4b5563',
    fontSize: 16,
    textAlign: 'center',
  },
});
