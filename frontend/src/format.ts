export const euroNumber = new Intl.NumberFormat("nl-BE", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  useGrouping: true,
});
export const overviewNumber = new Intl.NumberFormat("nl-BE", {
  maximumFractionDigits: 0,
  useGrouping: true,
});
export const compactOverviewNumber = new Intl.NumberFormat("nl-BE", {
  maximumFractionDigits: 0,
  useGrouping: false,
});

export const money = (value: string | number | null) => {
  if (value === null) return "—";
  const amount = Number(value);
  return `${amount < 0 ? "-€ " : "€ "}${euroNumber.format(Math.abs(amount))}`;
};

export const sumMoney = (values: string[]) =>
  (values.reduce((sum, value) => sum + Math.round(Number(value) * 100), 0) / 100).toFixed(2);

/** Euro amount rounded to whole euros, for compact labels such as chart values. */
export const wholeMoney = (value: number) =>
  `${value < 0 ? "-€ " : "€ "}${overviewNumber.format(Math.abs(Math.round(value)))}`;

/** Euro amount with cents only when it has them, for budgets that people type in. */
export const exactMoney = (value: number) =>
  Math.round(value * 100) % 100 === 0 ? wholeMoney(value) : money(value);
