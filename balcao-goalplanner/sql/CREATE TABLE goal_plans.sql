CREATE or replace TABLE goal_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    goal_value_today FLOAT NOT NULL,
    inflation_rate FLOAT NOT NULL,
    years_to_goal INT NOT NULL,
    rate_inv_return FLOAT NOT NULL,
    initial_corpus FLOAT NOT NULL,
    future_goal_value FLOAT NOT NULL,
    annual_sip FLOAT NOT NULL
);