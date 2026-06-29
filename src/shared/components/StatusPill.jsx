import { StyleSheet, Text, View } from 'react-native';

import { getStatusLabel, getStatusTone } from '../constants/assetStatuses';

export function StatusPill({ status }) {
  const tone = getStatusTone(status);
  return (
    <View style={[styles.pill, styles[tone]]}>
      <Text style={[styles.label, styles[`${tone}Text`]]}>{getStatusLabel(status)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    alignSelf: 'flex-start',
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  label: {
    fontSize: 12,
    fontWeight: '700',
  },
  neutral: {
    backgroundColor: '#e2e8f0',
  },
  pending: {
    backgroundColor: '#fef3c7',
  },
  success: {
    backgroundColor: '#dcfce7',
  },
  danger: {
    backgroundColor: '#fee2e2',
  },
  neutralText: {
    color: '#334155',
  },
  pendingText: {
    color: '#92400e',
  },
  successText: {
    color: '#166534',
  },
  dangerText: {
    color: '#991b1b',
  },
});
