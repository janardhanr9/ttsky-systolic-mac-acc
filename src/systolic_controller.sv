module systolic_controller(
    input  logic [7:0] ui_in,    // Dedicated inputs
    output logic [7:0] uo_out,   // Dedicated outputs
    input  logic [7:0] uio_in,   // IOs: Input path
    output logic [7:0] uio_out,  // IOs: Output path
    output logic [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  logic       ena,      // always 1 when the design is powered, so you can ignore it
    input  logic       clk,      // clock
    input  logic       rst_n     // reset_n - low to reset
);

// Control signals for systolic array
logic [7:0] pe_acc_en;   
logic [7:0] pe_weight_en;
logic [7:0] pe_bias_en;    
logic [2:0] drain_sel;

logic [7:0] raw_acc_out;

// Instantiate the systolic array
systolic_array sa_inst (
    .clk           (clk),
    .rst_n         (rst_n),
    .data_in       (ui_in[3:0]),
    .weight_in     (ui_in[3:0]),
    .bias_in       (ui_in[3:0]),
    .pe_acc_en     (pe_acc_en),
    .pe_weight_en  (pe_weight_en),
    .pe_bias_en    (pe_bias_en),
    .drain_sel     (drain_sel),
    .final_acc_out (raw_acc_out)
);

assign uo_out  = raw_acc_out;
assign uio_out = 8'b0;
assign uio_oe  = 8'b0;  // Configure as inputs (not used)

// Suppress warnings for unused inputs and outputs
logic _unused = &{ena, uio_in, ui_in[7:4], uio_out, 1'b0};

typedef enum logic [2:0] {IDLE, LOAD_W, LOAD_B, COMPUTE, DRAIN} state_t;
state_t state, next_state;
logic [3:0] cycle_count;

// Combinational logic for next state
always_comb begin
    next_state = state;
    
    case (state)
        IDLE: begin
            next_state = LOAD_W;
        end
        LOAD_W: begin
            if (cycle_count == 4'd7) begin
                next_state = LOAD_B;
            end
        end
        LOAD_B: begin
            if (cycle_count == 4'd7) begin
                next_state = COMPUTE;
            end
        end
        COMPUTE: begin
            if (cycle_count == 4'd14) begin
                next_state = DRAIN;
            end
        end
        DRAIN: begin
            if (cycle_count == 4'd7) begin
                next_state = IDLE;
            end
        end
        default: next_state = IDLE;
    endcase
end

// Combinational logic for control signals
always_comb begin
    pe_acc_en = 8'b00000000;
    pe_weight_en = 8'b00000000;
    pe_bias_en = 8'b00000000;
    drain_sel = 3'b000;

    case (state)
        LOAD_W: begin
            pe_weight_en[cycle_count[2:0]] = 1'b1;
        end
        LOAD_B: begin
            pe_bias_en[cycle_count[2:0]] = 1'b1;
        end
        COMPUTE: begin
            for (int i = 0; i < 8; i++) begin
                if (cycle_count >= 4'(i) && cycle_count < 4'(i + 8))
                    pe_acc_en[i] = 1'b1;
            end
        end
        DRAIN: begin
            drain_sel = cycle_count[2:0];
        end
        default: begin
        end
    endcase
end

// Sequential logic
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state <= IDLE;
        cycle_count <= 4'd0;
    end else begin
        state <= next_state;
        // Restart the counter on every state change, otherwise keep counting
        // within the current state. This keeps cycle_count == 0 on the first
        // cycle of each state, which the control decode above relies on.
        if (next_state != state) begin
            cycle_count <= 4'd0;
        end else begin
            cycle_count <= cycle_count + 4'd1;
        end
    end
end

endmodule