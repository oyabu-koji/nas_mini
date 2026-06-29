import { Pressable, StyleSheet, Text } from 'react-native';

export function ActionButton({ label, onPress, disabled = false, variant = 'primary' }) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        styles[variant],
        disabled && styles.disabled,
        pressed && !disabled && styles.pressed,
      ]}
    >
      <Text style={[styles.label, variant === 'secondary' && styles.secondaryLabel]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  primary: {
    backgroundColor: '#155e75',
  },
  secondary: {
    borderWidth: 1,
    borderColor: '#94a3b8',
    backgroundColor: '#ffffff',
  },
  danger: {
    backgroundColor: '#b91c1c',
  },
  disabled: {
    opacity: 0.45,
  },
  pressed: {
    opacity: 0.78,
  },
  label: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '700',
    textAlign: 'center',
  },
  secondaryLabel: {
    color: '#0f172a',
  },
});
